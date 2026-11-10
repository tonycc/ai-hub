"""Lifecycle of one semantic package version.

    DRAFT -> REVIEWED -> VERIFIED -> PUBLISHED -> DEPRECATED -> RETIRED

The content of a version never changes; only this state does. Generated and
imported versions start in DRAFT and can only move forward through the gates
below, each attributed to a named actor:

- review:  a technical reviewer confirms the package validates against the
           registered contracts.
- verify:  the data owner confirms each active binding (meaning, source and
           sensitivity), then the package as a whole.
- publish: freezes the canonical content and its digest as the snapshot that
           model builds and MCP read.

Changing content means importing a new version, which starts again at DRAFT.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

from ai_hub_platform.modules.semantic.metamodel import SemanticPackage
from ai_hub_platform.modules.semantic.validator import ValidationIssue, has_errors
from ai_hub_platform.modules.semantic.versioning import (
    binding_digest,
    canonical_package_json,
    package_digest,
)

PackageStatus = Literal["DRAFT", "REVIEWED", "VERIFIED", "PUBLISHED", "DEPRECATED", "RETIRED"]
BindingStatus = Literal["DRAFT", "REVIEWED", "VERIFIED"]
Origin = Literal["GENERATED", "IMPORTED", "SEED"]

# Statuses whose content MCP and model builds may read.
CONSUMABLE_STATUSES: frozenset[str] = frozenset({"PUBLISHED", "DEPRECATED"})


class LifecycleError(ValueError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class BindingVerification:
    binding_id: str
    digest: str
    status: BindingStatus = "DRAFT"
    verified_by: str | None = None
    verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PackageVersionState:
    package_id: str
    version: str
    digest: str
    origin: Origin
    status: PackageStatus = "DRAFT"
    bindings: dict[str, BindingVerification] = field(default_factory=dict[str, BindingVerification])
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    verified_by: str | None = None
    verified_at: datetime | None = None
    published_by: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PublishedSnapshot:
    """Immutable published content: what downstream reads and can re-verify."""

    package_id: str
    version: str
    digest: str
    canonical_json: str
    binding_digests: dict[str, str]
    published_by: str
    published_at: datetime


def new_version_state(package: SemanticPackage, *, origin: Origin) -> PackageVersionState:
    return PackageVersionState(
        package_id=package.package_id,
        version=package.version,
        digest=package_digest(package),
        origin=origin,
        bindings={
            binding.binding_id: BindingVerification(binding.binding_id, binding_digest(binding))
            for binding in package.active_bindings()
        },
    )


def _require_actor(actor: str) -> str:
    cleaned = actor.strip()
    if not cleaned:
        raise LifecycleError("actor_required", "an attributed actor is required")
    return cleaned


def _require_status(state: PackageVersionState, expected: PackageStatus, action: str) -> None:
    if state.status != expected:
        raise LifecycleError(
            "invalid_transition",
            f"{action} requires status {expected}; {state.package_id}@{state.version} "
            f"is {state.status}",
        )


def _require_same_content(state: PackageVersionState, package: SemanticPackage) -> None:
    if package_digest(package) != state.digest:
        raise LifecycleError(
            "content_changed",
            "package content does not match this version; import it as a new version",
        )


def review(
    state: PackageVersionState,
    package: SemanticPackage,
    issues: list[ValidationIssue],
    *,
    actor: str,
    at: datetime,
) -> PackageVersionState:
    reviewer = _require_actor(actor)
    _require_status(state, "DRAFT", "review")
    _require_same_content(state, package)
    if has_errors(issues):
        raise LifecycleError(
            "validation_failed", "package has validation errors and cannot be reviewed"
        )
    return replace(
        state,
        status="REVIEWED",
        reviewed_by=reviewer,
        reviewed_at=at,
        bindings={
            binding_id: replace(binding, status="REVIEWED")
            for binding_id, binding in state.bindings.items()
        },
    )


def verify_binding(
    state: PackageVersionState, binding_id: str, *, actor: str, at: datetime
) -> PackageVersionState:
    owner = _require_actor(actor)
    _require_status(state, "REVIEWED", "binding verification")
    binding = state.bindings.get(binding_id)
    if binding is None:
        raise LifecycleError("binding_not_found", f"no active binding {binding_id}")
    updated = dict(state.bindings)
    updated[binding_id] = replace(binding, status="VERIFIED", verified_by=owner, verified_at=at)
    return replace(state, bindings=updated)


def verify(state: PackageVersionState, *, actor: str, at: datetime) -> PackageVersionState:
    owner = _require_actor(actor)
    _require_status(state, "REVIEWED", "verify")
    pending = sorted(
        binding_id for binding_id, binding in state.bindings.items() if binding.status != "VERIFIED"
    )
    if pending:
        raise LifecycleError(
            "bindings_not_verified", f"bindings awaiting data owner verification: {pending}"
        )
    return replace(state, status="VERIFIED", verified_by=owner, verified_at=at)


def publish(
    state: PackageVersionState,
    package: SemanticPackage,
    *,
    actor: str,
    at: datetime,
) -> tuple[PackageVersionState, PublishedSnapshot]:
    publisher = _require_actor(actor)
    _require_status(state, "VERIFIED", "publish")
    _require_same_content(state, package)
    for binding in package.active_bindings():
        recorded = state.bindings.get(binding.binding_id)
        if recorded is None or recorded.digest != binding_digest(binding):
            raise LifecycleError(
                "binding_changed",
                f"binding {binding.binding_id} differs from the verified content",
            )
    snapshot = PublishedSnapshot(
        package_id=state.package_id,
        version=state.version,
        digest=state.digest,
        canonical_json=canonical_package_json(package),
        binding_digests={
            binding_id: binding.digest for binding_id, binding in state.bindings.items()
        },
        published_by=publisher,
        published_at=at,
    )
    return replace(state, status="PUBLISHED", published_by=publisher, published_at=at), snapshot


def deprecate(state: PackageVersionState) -> PackageVersionState:
    _require_status(state, "PUBLISHED", "deprecate")
    return replace(state, status="DEPRECATED")


def retire(state: PackageVersionState) -> PackageVersionState:
    _require_status(state, "DEPRECATED", "retire")
    return replace(state, status="RETIRED")


def is_consumable(state: PackageVersionState) -> bool:
    """Whether MCP and model builds may read this version's snapshot."""
    return state.status in CONSUMABLE_STATUSES
