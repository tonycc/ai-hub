"""PostgreSQL storage for semantic package versions.

Content is written once and never updated; only lifecycle columns change, and
every state write is guarded by ``lock_version`` so two reviewers cannot both
advance the same version. All statements run in the caller's transaction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.semantic.generator import ContractInput
from ai_hub_platform.modules.semantic.lifecycle import (
    BindingStatus,
    BindingVerification,
    Origin,
    PackageStatus,
    PackageVersionState,
    PublishedSnapshot,
)
from ai_hub_platform.modules.semantic.loader import load_package_document
from ai_hub_platform.modules.semantic.metamodel import SemanticPackage
from ai_hub_platform.modules.semantic.validator import ContractKey, ContractView

_VERSION_COLUMNS = """
    version_id, package_id, version, display_name, digest, origin, status,
    content, warnings, created_by, created_at, reviewed_by, reviewed_at,
    verified_by, verified_at, published_by, published_at, updated_at, lock_version
"""


class SemanticStoreError(ValueError):
    def __init__(self, error_code: str, message: str, *, status_code: int = 409) -> None:
        self.error_code = error_code
        self.status_code = status_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PackageVersionRow:
    version_id: UUID
    display_name: str
    content: dict[str, Any]
    warnings: list[dict[str, Any]]
    created_by: str
    created_at: datetime
    updated_at: datetime
    lock_version: int
    state: PackageVersionState

    def package(self) -> SemanticPackage:
        return load_package_document(self.content)


@dataclass(frozen=True, slots=True)
class PackageSummary:
    package_id: str
    display_name: str
    version_count: int
    latest_version: str
    latest_status: str
    published_version: str | None
    updated_at: datetime


def _as_uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _row_to_version(row: Any, bindings: dict[str, BindingVerification]) -> PackageVersionRow:
    return PackageVersionRow(
        version_id=_as_uuid(row.version_id),
        display_name=str(row.display_name),
        content=dict(row.content),
        warnings=[dict(item) for item in cast(list[dict[str, Any]], row.warnings or [])],
        created_by=str(row.created_by),
        created_at=row.created_at,
        updated_at=row.updated_at,
        lock_version=int(row.lock_version),
        state=PackageVersionState(
            package_id=str(row.package_id),
            version=str(row.version),
            digest=str(row.digest),
            origin=cast(Origin, str(row.origin)),
            status=cast(PackageStatus, str(row.status)),
            bindings=bindings,
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            verified_by=row.verified_by,
            verified_at=row.verified_at,
            published_by=row.published_by,
            published_at=row.published_at,
        ),
    )


class SemanticStore:
    async def create_version(
        self,
        session: AsyncSession,
        package: SemanticPackage,
        state: PackageVersionState,
        *,
        created_by: str,
        warnings: Sequence[Mapping[str, Any]] = (),
    ) -> PackageVersionRow:
        version_id = uuid4()
        try:
            async with session.begin_nested():
                await session.execute(
                    text(
                        """
                        INSERT INTO semantic.package_version (
                            version_id, package_id, version, display_name, digest,
                            origin, status, content, warnings, created_by
                        ) VALUES (
                            :version_id, :package_id, :version, :display_name, :digest,
                            :origin, :status, CAST(:content AS jsonb),
                            CAST(:warnings AS jsonb), :created_by
                        )
                        """
                    ),
                    {
                        "version_id": version_id,
                        "package_id": state.package_id,
                        "version": state.version,
                        "display_name": package.display_name,
                        "digest": state.digest,
                        "origin": state.origin,
                        "status": state.status,
                        "content": json.dumps(package.model_dump(mode="json"), ensure_ascii=False),
                        "warnings": json.dumps([dict(item) for item in warnings]),
                        "created_by": created_by,
                    },
                )
        except IntegrityError as error:
            raise SemanticStoreError(
                "semantic_version_exists",
                f"{state.package_id}@{state.version} already exists; "
                "content is immutable, import under a new version",
            ) from error
        for binding in state.bindings.values():
            await session.execute(
                text(
                    """
                    INSERT INTO semantic.binding_verification (
                        version_id, binding_id, digest, status
                    ) VALUES (:version_id, :binding_id, :digest, :status)
                    """
                ),
                {
                    "version_id": version_id,
                    "binding_id": binding.binding_id,
                    "digest": binding.digest,
                    "status": binding.status,
                },
            )
        created = await self.get_version(session, version_id)
        if created is None:  # pragma: no cover - defensive
            raise SemanticStoreError("semantic_version_not_found", "failed to persist version")
        return created

    async def _bindings(
        self, session: AsyncSession, version_id: UUID
    ) -> dict[str, BindingVerification]:
        result = await session.execute(
            text(
                """
                SELECT binding_id, digest, status, verified_by, verified_at
                FROM semantic.binding_verification
                WHERE version_id = :version_id
                ORDER BY binding_id
                """
            ),
            {"version_id": version_id},
        )
        return {
            str(row.binding_id): BindingVerification(
                binding_id=str(row.binding_id),
                digest=str(row.digest),
                status=cast(BindingStatus, str(row.status)),
                verified_by=row.verified_by,
                verified_at=row.verified_at,
            )
            for row in result.all()
        }

    async def get_version(
        self, session: AsyncSession, version_id: UUID, *, for_update: bool = False
    ) -> PackageVersionRow | None:
        result = await session.execute(
            text(
                f"""
                SELECT {_VERSION_COLUMNS}
                FROM semantic.package_version
                WHERE version_id = :version_id
                {"FOR UPDATE" if for_update else ""}
                """
            ),
            {"version_id": version_id},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return _row_to_version(row, await self._bindings(session, version_id))

    async def list_versions(
        self, session: AsyncSession, package_id: str
    ) -> list[PackageVersionRow]:
        result = await session.execute(
            text(
                f"""
                SELECT {_VERSION_COLUMNS}
                FROM semantic.package_version
                WHERE package_id = :package_id
                ORDER BY created_at DESC, version DESC
                """
            ),
            {"package_id": package_id},
        )
        return [
            _row_to_version(row, await self._bindings(session, _as_uuid(row.version_id)))
            for row in result.all()
        ]

    async def list_packages(self, session: AsyncSession) -> list[PackageSummary]:
        result = await session.execute(
            text(
                """
                SELECT DISTINCT ON (latest.package_id)
                    latest.package_id, latest.display_name, latest.version AS latest_version,
                    latest.status AS latest_status, latest.updated_at,
                    (SELECT COUNT(*) FROM semantic.package_version AS counted
                      WHERE counted.package_id = latest.package_id) AS version_count,
                    (SELECT published.version FROM semantic.package_version AS published
                      WHERE published.package_id = latest.package_id
                        AND published.status = 'PUBLISHED') AS published_version
                FROM semantic.package_version AS latest
                ORDER BY latest.package_id, latest.created_at DESC, latest.version DESC
                """
            )
        )
        return [
            PackageSummary(
                package_id=str(row.package_id),
                display_name=str(row.display_name),
                version_count=int(row.version_count),
                latest_version=str(row.latest_version),
                latest_status=str(row.latest_status),
                published_version=row.published_version,
                updated_at=row.updated_at,
            )
            for row in result.all()
        ]

    async def published_version(
        self, session: AsyncSession, package_id: str, *, for_update: bool = False
    ) -> PackageVersionRow | None:
        result = await session.execute(
            text(
                f"""
                SELECT version_id
                FROM semantic.package_version
                WHERE package_id = :package_id AND status = 'PUBLISHED'
                {"FOR UPDATE" if for_update else ""}
                """
            ),
            {"package_id": package_id},
        )
        version_id = result.scalar_one_or_none()
        if version_id is None:
            return None
        return await self.get_version(session, _as_uuid(version_id))

    async def save_state(
        self,
        session: AsyncSession,
        row: PackageVersionRow,
        state: PackageVersionState,
        *,
        snapshot: PublishedSnapshot | None = None,
    ) -> PackageVersionRow:
        """Persist a lifecycle transition computed from ``row.state``."""
        result = await session.execute(
            text(
                """
                UPDATE semantic.package_version
                SET status = :status,
                    reviewed_by = :reviewed_by, reviewed_at = :reviewed_at,
                    verified_by = :verified_by, verified_at = :verified_at,
                    published_by = :published_by, published_at = :published_at,
                    published_snapshot = COALESCE(:snapshot, published_snapshot),
                    updated_at = CURRENT_TIMESTAMP,
                    lock_version = lock_version + 1
                WHERE version_id = :version_id AND lock_version = :lock_version
                """
            ),
            {
                "version_id": row.version_id,
                "lock_version": row.lock_version,
                "status": state.status,
                "reviewed_by": state.reviewed_by,
                "reviewed_at": state.reviewed_at,
                "verified_by": state.verified_by,
                "verified_at": state.verified_at,
                "published_by": state.published_by,
                "published_at": state.published_at,
                "snapshot": None if snapshot is None else snapshot.canonical_json,
            },
        )
        if int(getattr(result, "rowcount", 0) or 0) == 0:
            raise SemanticStoreError(
                "semantic_version_conflict", "version was changed concurrently; reload and retry"
            )
        for binding in state.bindings.values():
            await session.execute(
                text(
                    """
                    UPDATE semantic.binding_verification
                    SET status = :status, verified_by = :verified_by, verified_at = :verified_at
                    WHERE version_id = :version_id AND binding_id = :binding_id
                    """
                ),
                {
                    "version_id": row.version_id,
                    "binding_id": binding.binding_id,
                    "status": binding.status,
                    "verified_by": binding.verified_by,
                    "verified_at": binding.verified_at,
                },
            )
        saved = await self.get_version(session, row.version_id)
        if saved is None:  # pragma: no cover - defensive
            raise SemanticStoreError("semantic_version_not_found", "version disappeared")
        return saved

    async def published_snapshot(self, session: AsyncSession, version_id: UUID) -> str | None:
        result = await session.execute(
            text(
                """
                SELECT published_snapshot
                FROM semantic.package_version
                WHERE version_id = :version_id
                """
            ),
            {"version_id": version_id},
        )
        return result.scalar_one_or_none()

    async def load_contract_inputs(
        self, session: AsyncSession, keys: Sequence[ContractKey]
    ) -> dict[ContractKey, tuple[ContractInput, str]]:
        """Registered ingest contracts for ``keys`` with their status.

        Reads ``platform_core.ingest_contract`` directly: the semantic module
        consumes registered contracts but does not depend on ingest code.
        """
        found: dict[ContractKey, tuple[ContractInput, str]] = {}
        for source_application_id, object_type, contract_version in dict.fromkeys(keys):
            result = await session.execute(
                text(
                    """
                    SELECT json_schema, schema_fingerprint, field_classifications, status
                    FROM platform_core.ingest_contract
                    WHERE source_application_id = :source_application_id
                      AND object_type = :object_type
                      AND contract_version = :contract_version
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "contract_version": contract_version,
                },
            )
            row = result.one_or_none()
            if row is None:
                continue
            key = (source_application_id, object_type, contract_version)
            found[key] = (
                ContractInput(
                    source_application_id=source_application_id,
                    object_type=object_type,
                    contract_version=contract_version,
                    json_schema=dict(row.json_schema),
                    schema_fingerprint=str(row.schema_fingerprint),
                    field_classifications=dict(row.field_classifications or {}),
                ),
                str(row.status),
            )
        return found

    async def load_contract_views(
        self, session: AsyncSession, package: SemanticPackage
    ) -> dict[ContractKey, ContractView]:
        keys = [
            (binding.source_application_id, binding.object_type, binding.payload_contract_version)
            for binding in package.active_bindings()
        ]
        loaded = await self.load_contract_inputs(session, keys)
        return {
            key: ContractView(
                source_application_id=contract.source_application_id,
                object_type=contract.object_type,
                contract_version=contract.contract_version,
                json_schema=contract.json_schema,
                schema_fingerprint=contract.schema_fingerprint,
                status=status,
            )
            for key, (contract, status) in loaded.items()
        }
