"""Semantic center use cases: generate, import, validate and move a version
through its lifecycle. Each method runs inside the caller's transaction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.semantic import lifecycle
from ai_hub_platform.modules.semantic.generator import (
    ContractInput,
    GenerationError,
    generate_draft_package,
)
from ai_hub_platform.modules.semantic.lifecycle import LifecycleError, Origin
from ai_hub_platform.modules.semantic.loader import (
    DocumentFormat,
    PackageLoadError,
    load_package_text,
)
from ai_hub_platform.modules.semantic.metamodel import SemanticPackage
from ai_hub_platform.modules.semantic.store import (
    PackageSummary,
    PackageVersionRow,
    SemanticStore,
    SemanticStoreError,
)
from ai_hub_platform.modules.semantic.validator import (
    CompatibilityChange,
    ContractKey,
    ValidationIssue,
    breaking_changes,
    compare_packages,
    validate_package,
)


class SemanticServiceError(ValueError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, object] | None = None,
    ) -> None:
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ContractSelection:
    source_application_id: str
    object_type: str
    contract_version: str
    key_fields: tuple[str, ...] = ()

    @property
    def key(self) -> ContractKey:
        return (self.source_application_id, self.object_type, self.contract_version)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: list[ValidationIssue]
    changes: list[CompatibilityChange]
    compared_to_version: str | None

    @property
    def breaking(self) -> list[CompatibilityChange]:
        return breaking_changes(self.changes)


def _now() -> datetime:
    return datetime.now(tz=UTC)


class SemanticService:
    def __init__(self, store: SemanticStore | None = None) -> None:
        self.store = store or SemanticStore()

    async def list_packages(self, session: AsyncSession) -> list[PackageSummary]:
        return await self.store.list_packages(session)

    async def list_versions(
        self, session: AsyncSession, package_id: str
    ) -> list[PackageVersionRow]:
        return await self.store.list_versions(session, package_id)

    async def get_version(
        self, session: AsyncSession, version_id: UUID, *, for_update: bool = False
    ) -> PackageVersionRow:
        row = await self.store.get_version(session, version_id, for_update=for_update)
        if row is None:
            raise SemanticServiceError(
                "semantic_version_not_found", "semantic package version not found", status_code=404
            )
        return row

    async def _create(
        self,
        session: AsyncSession,
        package: SemanticPackage,
        *,
        origin: Origin,
        actor: str,
        warnings: Sequence[Mapping[str, object]] = (),
    ) -> PackageVersionRow:
        state = lifecycle.new_version_state(package, origin=origin)
        try:
            return await self.store.create_version(
                session, package, state, created_by=actor, warnings=warnings
            )
        except SemanticStoreError as error:
            raise SemanticServiceError(
                error.error_code, str(error), status_code=error.status_code
            ) from error

    async def generate(
        self,
        session: AsyncSession,
        *,
        package_id: str,
        display_name: str,
        version: str,
        owner: str,
        selections: Sequence[ContractSelection],
        actor: str,
    ) -> PackageVersionRow:
        """Generate a DRAFT version from ACTIVE ingest contracts."""
        loaded = await self.store.load_contract_inputs(
            session, [selection.key for selection in selections]
        )
        contracts: list[ContractInput] = []
        for selection in selections:
            entry = loaded.get(selection.key)
            if entry is None or entry[1] != "ACTIVE":
                raise SemanticServiceError(
                    "ingest_contract_not_active",
                    f"no ACTIVE ingest contract for {'/'.join(selection.key)}",
                    status_code=409,
                )
            contracts.append(entry[0])
        try:
            result = generate_draft_package(
                contracts,
                package_id=package_id,
                display_name=display_name,
                version=version,
                owner=owner,
                key_fields={
                    selection.object_type: selection.key_fields
                    for selection in selections
                    if selection.key_fields
                },
            )
        except (GenerationError, ValueError) as error:
            raise SemanticServiceError("semantic_generation_failed", str(error)) from error
        return await self._create(
            session,
            result.package,
            origin="GENERATED",
            actor=actor,
            warnings=[asdict(warning) for warning in result.warnings],
        )

    async def import_document(
        self,
        session: AsyncSession,
        *,
        document: str,
        document_format: DocumentFormat,
        actor: str,
        expected_package_id: str | None = None,
        origin: Origin = "IMPORTED",
    ) -> PackageVersionRow:
        try:
            package = load_package_text(document, document_format)
        except PackageLoadError as error:
            raise SemanticServiceError(
                "semantic_package_invalid",
                "semantic package document is invalid",
                details={"errors": error.errors},
            ) from error
        return await self.import_package(
            session,
            package,
            actor=actor,
            expected_package_id=expected_package_id,
            origin=origin,
        )

    async def import_package(
        self,
        session: AsyncSession,
        package: SemanticPackage,
        *,
        actor: str,
        expected_package_id: str | None = None,
        origin: Origin = "IMPORTED",
    ) -> PackageVersionRow:
        if expected_package_id is not None and package.package_id != expected_package_id:
            raise SemanticServiceError(
                "semantic_package_mismatch",
                f"document package_id {package.package_id} does not match {expected_package_id}",
            )
        return await self._create(session, package, origin=origin, actor=actor)

    async def validate(self, session: AsyncSession, row: PackageVersionRow) -> ValidationReport:
        package = row.package()
        contracts = await self.store.load_contract_views(session, package)
        issues = validate_package(package, contracts)
        published = await self.store.published_version(session, row.state.package_id)
        if published is None or published.version_id == row.version_id:
            return ValidationReport(issues=issues, changes=[], compared_to_version=None)
        return ValidationReport(
            issues=issues,
            changes=compare_packages(published.package(), package),
            compared_to_version=published.state.version,
        )

    async def _transition(
        self,
        session: AsyncSession,
        row: PackageVersionRow,
        state: lifecycle.PackageVersionState,
        snapshot: lifecycle.PublishedSnapshot | None = None,
    ) -> PackageVersionRow:
        try:
            return await self.store.save_state(session, row, state, snapshot=snapshot)
        except SemanticStoreError as error:
            raise SemanticServiceError(
                error.error_code, str(error), status_code=error.status_code
            ) from error

    async def review(
        self, session: AsyncSession, version_id: UUID, *, actor: str
    ) -> PackageVersionRow:
        row = await self.get_version(session, version_id, for_update=True)
        report = await self.validate(session, row)
        try:
            state = lifecycle.review(
                row.state, row.package(), report.issues, actor=actor, at=_now()
            )
        except LifecycleError as error:
            raise _lifecycle_error(error, report) from error
        return await self._transition(session, row, state)

    async def verify_binding(
        self, session: AsyncSession, version_id: UUID, binding_id: str, *, actor: str
    ) -> PackageVersionRow:
        row = await self.get_version(session, version_id, for_update=True)
        try:
            state = lifecycle.verify_binding(row.state, binding_id, actor=actor, at=_now())
        except LifecycleError as error:
            raise _lifecycle_error(error) from error
        return await self._transition(session, row, state)

    async def verify(
        self, session: AsyncSession, version_id: UUID, *, actor: str
    ) -> PackageVersionRow:
        row = await self.get_version(session, version_id, for_update=True)
        try:
            state = lifecycle.verify(row.state, actor=actor, at=_now())
        except LifecycleError as error:
            raise _lifecycle_error(error) from error
        return await self._transition(session, row, state)

    async def publish(
        self,
        session: AsyncSession,
        version_id: UUID,
        *,
        actor: str,
        acknowledge_breaking: bool = False,
    ) -> PackageVersionRow:
        """Publish a VERIFIED version and deprecate the one it replaces.

        Contracts are re-validated at publish time: a contract deactivated or
        changed since review must not reach consumers. Breaking changes against
        the currently published version need an explicit acknowledgement.
        """
        row = await self.get_version(session, version_id, for_update=True)
        report = await self.validate(session, row)
        if any(issue.severity == "error" for issue in report.issues):
            raise SemanticServiceError(
                "semantic_validation_failed",
                "package no longer validates against the registered contracts",
                status_code=409,
                details={"issues": [asdict(issue) for issue in report.issues]},
            )
        if report.breaking and not acknowledge_breaking:
            raise SemanticServiceError(
                "semantic_breaking_changes_unacknowledged",
                f"version has breaking changes against {report.compared_to_version}; "
                "review the impact report and acknowledge it to publish",
                status_code=409,
                details={"breaking_changes": [asdict(change) for change in report.breaking]},
            )
        try:
            state, snapshot = lifecycle.publish(row.state, row.package(), actor=actor, at=_now())
        except LifecycleError as error:
            raise _lifecycle_error(error) from error
        previous = await self.store.published_version(
            session, row.state.package_id, for_update=True
        )
        if previous is not None and previous.version_id != row.version_id:
            await self._transition(session, previous, lifecycle.deprecate(previous.state))
        return await self._transition(session, row, state, snapshot)

    async def deprecate(self, session: AsyncSession, version_id: UUID) -> PackageVersionRow:
        row = await self.get_version(session, version_id, for_update=True)
        try:
            state = lifecycle.deprecate(row.state)
        except LifecycleError as error:
            raise _lifecycle_error(error) from error
        return await self._transition(session, row, state)

    async def retire(self, session: AsyncSession, version_id: UUID) -> PackageVersionRow:
        row = await self.get_version(session, version_id, for_update=True)
        try:
            state = lifecycle.retire(row.state)
        except LifecycleError as error:
            raise _lifecycle_error(error) from error
        return await self._transition(session, row, state)


def _lifecycle_error(
    error: LifecycleError, report: ValidationReport | None = None
) -> SemanticServiceError:
    details: dict[str, object] = {}
    if report is not None and error.error_code == "validation_failed":
        details["issues"] = [asdict(issue) for issue in report.issues]
    return SemanticServiceError(
        f"semantic_{error.error_code}", str(error), status_code=409, details=details
    )
