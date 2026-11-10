"""Semantic service orchestration, API wiring and the schema migration."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from ai_hub_platform.api import semantics as semantics_api
from ai_hub_platform.main import create_app
from ai_hub_platform.modules.semantic.generator import ContractInput
from ai_hub_platform.modules.semantic.lifecycle import PackageVersionState, PublishedSnapshot
from ai_hub_platform.modules.semantic.metamodel import SemanticPackage
from ai_hub_platform.modules.semantic.service import (
    ContractSelection,
    SemanticService,
    SemanticServiceError,
)
from ai_hub_platform.modules.semantic.store import (
    PackageVersionRow,
    SemanticStore,
    SemanticStoreError,
)
from ai_hub_platform.modules.semantic.validator import ContractKey
from ai_hub_platform.operations.release import parse_migration
from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SESSION = cast(Any, None)
NOW = datetime(2026, 11, 10, tzinfo=UTC)
ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ITEM_CODE": {"type": "string"},
        "ITEM_NAME": {"type": "string"},
        "BRAND": {"type": "string"},
    },
    "required": ["ITEM_CODE", "ITEM_NAME"],
}
ITEM_KEY: ContractKey = ("erp-adapter", "erp.item", "item.v1")
SELECTION = ContractSelection(*ITEM_KEY, key_fields=("ITEM_CODE",))


class MemoryStore(SemanticStore):
    """In-memory stand-in with the same optimistic-lock and uniqueness rules."""

    def __init__(self) -> None:
        self.rows: dict[UUID, PackageVersionRow] = {}
        self.snapshots: dict[UUID, str] = {}
        self.contracts: dict[ContractKey, tuple[ContractInput, str]] = {
            ITEM_KEY: (
                ContractInput(
                    source_application_id=ITEM_KEY[0],
                    object_type=ITEM_KEY[1],
                    contract_version=ITEM_KEY[2],
                    json_schema=ITEM_SCHEMA,
                    schema_fingerprint="1" * 64,
                ),
                "ACTIVE",
            )
        }

    async def create_version(
        self,
        session: Any,
        package: SemanticPackage,
        state: PackageVersionState,
        *,
        created_by: str,
        warnings: Sequence[Mapping[str, Any]] = (),
    ) -> PackageVersionRow:
        if any(
            row.state.package_id == state.package_id and row.state.version == state.version
            for row in self.rows.values()
        ):
            raise SemanticStoreError("semantic_version_exists", "exists")
        row = PackageVersionRow(
            version_id=uuid4(),
            display_name=package.display_name,
            content=package.model_dump(mode="json"),
            warnings=[dict(item) for item in warnings],
            created_by=created_by,
            created_at=NOW,
            updated_at=NOW,
            lock_version=0,
            state=state,
        )
        self.rows[row.version_id] = row
        return row

    async def get_version(
        self, session: Any, version_id: UUID, *, for_update: bool = False
    ) -> PackageVersionRow | None:
        return self.rows.get(version_id)

    async def published_version(
        self, session: Any, package_id: str, *, for_update: bool = False
    ) -> PackageVersionRow | None:
        published = [
            row
            for row in self.rows.values()
            if row.state.package_id == package_id and row.state.status == "PUBLISHED"
        ]
        assert len(published) <= 1
        return published[0] if published else None

    async def save_state(
        self,
        session: Any,
        row: PackageVersionRow,
        state: PackageVersionState,
        *,
        snapshot: PublishedSnapshot | None = None,
    ) -> PackageVersionRow:
        current = self.rows[row.version_id]
        if current.lock_version != row.lock_version:
            raise SemanticStoreError("semantic_version_conflict", "changed concurrently")
        if snapshot is not None:
            self.snapshots[row.version_id] = snapshot.canonical_json
        saved = replace(current, state=state, lock_version=current.lock_version + 1)
        self.rows[row.version_id] = saved
        return saved

    async def load_contract_inputs(
        self, session: Any, keys: Sequence[ContractKey]
    ) -> dict[ContractKey, tuple[ContractInput, str]]:
        return {key: self.contracts[key] for key in keys if key in self.contracts}


async def _generate(service: SemanticService, version: str = "0.1.0") -> PackageVersionRow:
    return await service.generate(
        SESSION,
        package_id="demo",
        display_name="Demo",
        version=version,
        owner="data-office",
        selections=[SELECTION],
        actor="alice",
    )


async def _publish(service: SemanticService, row: PackageVersionRow, **kwargs: Any) -> Any:
    row = await service.review(SESSION, row.version_id, actor="alice")
    for binding_id in row.state.bindings:
        await service.verify_binding(SESSION, row.version_id, binding_id, actor="bob")
    await service.verify(SESSION, row.version_id, actor="bob")
    return await service.publish(SESSION, row.version_id, actor="carol", **kwargs)


async def _import_changed(
    service: SemanticService, base: PackageVersionRow, version: str, *, drop: str | None
) -> PackageVersionRow:
    document = json.loads(json.dumps(base.content))
    document["version"] = version
    if drop is not None:
        item = document["objects"][0]
        item["properties"] = [prop for prop in item["properties"] if prop["name"] != drop]
        del document["bindings"][0]["field_map"][drop]
    return await service.import_document(
        SESSION,
        document=json.dumps(document),
        document_format="json",
        actor="alice",
        expected_package_id="demo",
    )


@pytest.mark.asyncio
async def test_generate_requires_active_contracts_and_starts_as_draft() -> None:
    store = MemoryStore()
    service = SemanticService(store)
    row = await _generate(service)
    assert row.state.status == "DRAFT" and row.state.origin == "GENERATED"
    assert row.created_by == "alice"
    assert [warning["code"] for warning in row.warnings] == ["naming_unreviewed"]

    store.contracts[ITEM_KEY] = (store.contracts[ITEM_KEY][0], "DRAFT")
    with pytest.raises(SemanticServiceError) as raised:
        await _generate(service, "0.2.0")
    assert raised.value.error_code == "ingest_contract_not_active"

    with pytest.raises(SemanticServiceError) as raised:
        await service.generate(
            SESSION,
            package_id="demo",
            display_name="Demo",
            version="0.3.0",
            owner="",
            selections=[ContractSelection("erp-adapter", "erp.unknown", "v1")],
            actor="alice",
        )
    assert raised.value.error_code == "ingest_contract_not_active"


@pytest.mark.asyncio
async def test_versions_are_immutable_and_import_checks_the_target_package() -> None:
    service = SemanticService(MemoryStore())
    row = await _generate(service)
    with pytest.raises(SemanticServiceError) as raised:
        await _generate(service)
    assert raised.value.error_code == "semantic_version_exists"

    with pytest.raises(SemanticServiceError) as raised:
        await service.import_document(
            SESSION,
            document=json.dumps({**row.content, "version": "9"}),
            document_format="json",
            actor="alice",
            expected_package_id="other",
        )
    assert raised.value.error_code == "semantic_package_mismatch"

    with pytest.raises(SemanticServiceError) as raised:
        await service.import_document(
            SESSION, document="objects: [", document_format="yaml", actor="alice"
        )
    assert raised.value.error_code == "semantic_package_invalid"
    assert raised.value.details["errors"]


@pytest.mark.asyncio
async def test_review_reports_validation_issues_when_the_contract_drifts() -> None:
    store = MemoryStore()
    service = SemanticService(store)
    row = await _generate(service)
    contract = store.contracts[ITEM_KEY][0]
    store.contracts[ITEM_KEY] = (replace(contract, schema_fingerprint="9" * 64), "ACTIVE")

    with pytest.raises(SemanticServiceError) as raised:
        await service.review(SESSION, row.version_id, actor="alice")
    assert raised.value.error_code == "semantic_validation_failed"
    issues = cast(list[dict[str, Any]], raised.value.details["issues"])
    assert [issue["code"] for issue in issues] == ["contract_fingerprint_mismatch"]
    assert store.rows[row.version_id].state.status == "DRAFT"


@pytest.mark.asyncio
async def test_publish_stores_a_snapshot_and_deprecates_the_previous_version() -> None:
    store = MemoryStore()
    service = SemanticService(store)
    first = await _publish(service, await _generate(service))
    assert first.state.status == "PUBLISHED"
    assert json.loads(store.snapshots[first.version_id])["package"]["version"] == "0.1.0"

    second = await _import_changed(service, first, "0.2.0", drop=None)
    report = await service.validate(SESSION, second)
    assert report.compared_to_version == "0.1.0" and report.changes == []
    second = await _publish(service, second)

    assert second.state.status == "PUBLISHED"
    assert store.rows[first.version_id].state.status == "DEPRECATED"
    published = await store.published_version(SESSION, "demo")
    assert published is not None and published.version_id == second.version_id


@pytest.mark.asyncio
async def test_breaking_changes_need_an_explicit_acknowledgement() -> None:
    store = MemoryStore()
    service = SemanticService(store)
    first = await _publish(service, await _generate(service))
    second = await _import_changed(service, first, "0.2.0", drop="brand")

    with pytest.raises(SemanticServiceError) as raised:
        await _publish(service, second)
    assert raised.value.error_code == "semantic_breaking_changes_unacknowledged"
    breaking = cast(list[dict[str, Any]], raised.value.details["breaking_changes"])
    assert [change["code"] for change in breaking] == ["property_removed"]
    assert store.rows[first.version_id].state.status == "PUBLISHED"
    assert store.rows[second.version_id].state.status == "VERIFIED"

    published = await service.publish(
        SESSION, second.version_id, actor="carol", acknowledge_breaking=True
    )
    assert published.state.status == "PUBLISHED"
    assert store.rows[first.version_id].state.status == "DEPRECATED"


@pytest.mark.asyncio
async def test_publish_revalidates_contracts_that_changed_after_review() -> None:
    store = MemoryStore()
    service = SemanticService(store)
    row = await _generate(service)
    row = await service.review(SESSION, row.version_id, actor="alice")
    for binding_id in row.state.bindings:
        await service.verify_binding(SESSION, row.version_id, binding_id, actor="bob")
    await service.verify(SESSION, row.version_id, actor="bob")

    store.contracts[ITEM_KEY] = (store.contracts[ITEM_KEY][0], "DEPRECATED")
    with pytest.raises(SemanticServiceError) as raised:
        await service.publish(SESSION, row.version_id, actor="carol")
    assert raised.value.error_code == "semantic_validation_failed"
    assert store.rows[row.version_id].state.status == "VERIFIED"


@pytest.mark.asyncio
async def test_lifecycle_errors_and_missing_versions_map_to_api_errors() -> None:
    service = SemanticService(MemoryStore())
    row = await _generate(service)
    with pytest.raises(SemanticServiceError) as raised:
        await service.publish(SESSION, row.version_id, actor="carol")
    assert (raised.value.error_code, raised.value.status_code) == (
        "semantic_invalid_transition",
        409,
    )
    with pytest.raises(SemanticServiceError) as raised:
        await service.get_version(SESSION, uuid4())
    assert (raised.value.error_code, raised.value.status_code) == (
        "semantic_version_not_found",
        404,
    )


def _route_permissions() -> dict[tuple[str, str], tuple[str, bool]]:
    found: dict[tuple[str, str], tuple[str, bool]] = {}
    for route in semantics_api.router.routes:
        dependant = cast(Any, route).dependant
        for dependency in dependant.dependencies:
            closure = inspect.getclosurevars(dependency.call).nonlocals
            if "permission_code" not in closure:
                continue
            for method in cast(Any, route).methods:
                found[(method, cast(Any, route).path)] = (
                    closure["permission_code"],
                    closure["require_csrf"],
                )
    return found


def test_each_lifecycle_gate_has_its_own_permission_and_writes_need_csrf() -> None:
    base = "/portal-api/v1/semantics"
    permissions = _route_permissions()
    assert permissions == {
        ("GET", f"{base}/packages"): ("platform.semantic.read", False),
        ("GET", f"{base}/packages/{{package_id}}/versions"): ("platform.semantic.read", False),
        ("GET", f"{base}/versions/{{version_id}}"): ("platform.semantic.read", False),
        ("GET", f"{base}/versions/{{version_id}}/export"): ("platform.semantic.read", False),
        ("POST", f"{base}/versions/{{version_id}}/validate"): ("platform.semantic.read", False),
        ("POST", f"{base}/generations"): ("platform.semantic.write", True),
        ("POST", f"{base}/packages/{{package_id}}/versions"): ("platform.semantic.write", True),
        ("POST", f"{base}/versions/{{version_id}}/review"): ("platform.semantic.review", True),
        ("POST", f"{base}/versions/{{version_id}}/bindings/verify"): (
            "platform.semantic.verify",
            True,
        ),
        ("POST", f"{base}/versions/{{version_id}}/verify"): ("platform.semantic.verify", True),
        ("POST", f"{base}/versions/{{version_id}}/publish"): ("platform.semantic.publish", True),
        ("POST", f"{base}/versions/{{version_id}}/deprecate"): (
            "platform.semantic.publish",
            True,
        ),
        ("POST", f"{base}/versions/{{version_id}}/retire"): ("platform.semantic.publish", True),
    }
    paths = cast(dict[str, Any], create_app().openapi()["paths"])
    assert {path for _, path in permissions} <= set(paths)


def test_semantic_migration_is_expand_only_and_grants_least_privilege() -> None:
    from io import StringIO

    path = BACKEND_ROOT / "migrations/versions/core/20261110_core_0030.py"
    revision = parse_migration(path, "core")
    assert revision.phase == "expand"
    assert revision.down_revision == "20261016_core_0029"
    assert revision.rollback_compatible_with == frozenset({"20261016_core_0029"})
    assert revision.dangerous_operations == ()

    output = StringIO()
    command.upgrade(
        Config(BACKEND_ROOT / "alembic.ini", output_buffer=output),
        "20261016_core_0029:20261110_core_0030",
        sql=True,
    )
    sql = output.getvalue()
    assert "CREATE SCHEMA IF NOT EXISTS semantic" in sql
    assert "CREATE TABLE semantic.package_version" in sql
    assert "CREATE TABLE semantic.binding_verification" in sql
    assert "uq_package_version_one_published" in sql
    assert "CREATE TRIGGER package_version_guard" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON semantic.package_version TO ai_hub_platform" in sql
    assert "DELETE" not in sql.split("GRANT USAGE ON SCHEMA semantic")[1].split("INSERT INTO")[0]
    assert "ai_hub_raw" not in sql
    assert "platform_raw" not in sql
    for permission in ("read", "write", "review", "verify", "publish"):
        assert f"'platform.semantic.{permission}'" in sql
    # Operators can prepare and review, but only administrators verify and publish.
    assert "('PLATFORM_OPERATOR', 'platform.semantic.verify')" not in sql
    assert "('PLATFORM_OPERATOR', 'platform.semantic.publish')" not in sql
