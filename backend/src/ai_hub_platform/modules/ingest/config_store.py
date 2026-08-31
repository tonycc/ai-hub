"""Authoritative ingest configuration stored in platform_core (design §2.5.1).

Sources and the global policy are portal-managed. Readers include the portal API
(ai_hub_platform) and the ingest scheduler / one-shot CLIs (ai_hub_raw). The
operations JSON document remains only a bootstrap seed for fresh environments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.ingest.sources import (
    CHANGE_RECORD_PURPOSE_UNIQUE,
    ContractValidationMode,
    IngestSourceConfig,
    TransportMode,
)

_SOURCE_COLUMNS = """
    source_application_id, object_type, export_base_url,
    interval_seconds, lookback_versions, page_limit, enabled,
    transport_mode, push_protocol_version, contract_validation_mode,
    allow_empty_full, updated_at
"""


class IngestConfigError(ValueError):
    error_code = "invalid_ingest_config"


class IngestEnforceNotCertifiedError(IngestConfigError):
    error_code = "ingest_enforce_not_certified"


class IngestTransportImmutableError(IngestConfigError):
    error_code = "ingest_transport_mode_immutable"


class IngestTransportBusyError(IngestConfigError):
    error_code = "ingest_transport_mode_busy"


class IngestPushNotIsolatedError(IngestConfigError):
    error_code = "ingest_push_change_log_not_isolated"


@dataclass(frozen=True, slots=True)
class IngestPolicy:
    retention_keep_versions: int
    retention_keep_days: int | None
    payload_max_bytes: int
    page_limit_default: int
    page_limit_max: int
    scheduled_reconcile_enabled: bool
    reconcile_interval_hours: int
    push_staging_retention_hours: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IngestSourceRow:
    config: IngestSourceConfig
    updated_at: datetime


def _enabling_push_source(
    existing: IngestSourceRow | None, config: IngestSourceConfig
) -> bool:
    if config.transport_mode != "PUSH_AGENT" or not config.enabled:
        return False
    if existing is None:
        return True
    if existing.config.transport_mode != "PUSH_AGENT":
        return True
    return not existing.config.enabled


def _switching_pull_to_enforce(
    existing: IngestSourceRow | None, config: IngestSourceConfig
) -> bool:
    if config.transport_mode != "PULL_EXPORT":
        return False
    if config.contract_validation_mode != "ENFORCE":
        return False
    if existing is None:
        return True
    if existing.config.transport_mode != "PULL_EXPORT":
        return True
    if existing.config.contract_validation_mode != "ENFORCE":
        return True
    return not existing.config.enabled


def _row_to_source(row: Any) -> IngestSourceRow:
    export_base_url = row.export_base_url
    return IngestSourceRow(
        config=IngestSourceConfig(
            source_application_id=str(row.source_application_id),
            object_type=str(row.object_type),
            export_base_url=None if export_base_url is None else str(export_base_url),
            interval_seconds=int(row.interval_seconds),
            lookback_versions=int(row.lookback_versions),
            page_limit=int(row.page_limit),
            enabled=bool(row.enabled),
            transport_mode=cast(TransportMode, str(row.transport_mode)),
            push_protocol_version=(
                None
                if row.push_protocol_version is None
                else str(row.push_protocol_version)
            ),
            contract_validation_mode=cast(
                ContractValidationMode, str(row.contract_validation_mode)
            ),
            allow_empty_full=bool(row.allow_empty_full),
        ),
        updated_at=row.updated_at,
    )


class IngestConfigStore:
    """CRUD over platform_core ingest configuration within the caller's transaction."""

    async def list_sources(self, session: AsyncSession) -> list[IngestSourceRow]:
        result = await session.execute(
            text(
                f"""
                SELECT {_SOURCE_COLUMNS}
                FROM platform_core.ingest_source
                ORDER BY source_application_id, object_type
                """
            )
        )
        return [_row_to_source(row) for row in result.all()]

    async def list_enabled_source_configs(
        self, session: AsyncSession
    ) -> list[IngestSourceConfig]:
        rows = await self.list_sources(session)
        return [row.config for row in rows if row.config.enabled]

    async def get_source(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
    ) -> IngestSourceRow | None:
        result = await session.execute(
            text(
                f"""
                SELECT {_SOURCE_COLUMNS}
                FROM platform_core.ingest_source
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
            },
        )
        row = result.one_or_none()
        return _row_to_source(row) if row is not None else None

    async def upsert_source(
        self, session: AsyncSession, config: IngestSourceConfig
    ) -> IngestSourceRow:
        existing = await self.get_source(
            session,
            source_application_id=config.source_application_id,
            object_type=config.object_type,
        )
        if (
            existing is not None
            and existing.config.transport_mode != config.transport_mode
        ):
            if existing.config.enabled or config.enabled:
                raise IngestTransportImmutableError(
                    "transport_mode cannot change while the source is enabled; "
                    "disable it first, wait for in-flight work to finish, then switch"
                )
        if _enabling_push_source(existing, config) and not CHANGE_RECORD_PURPOSE_UNIQUE:
            raise IngestPushNotIsolatedError(
                "enabled PUSH_AGENT requires the contract migration that isolates "
                "certification change records from the production unique key"
            )
        if _switching_pull_to_enforce(existing, config) or _enabling_push_source(
            existing, config
        ):
            await self._require_approved_certification(session, config)
        await session.execute(
            text(
                """
                INSERT INTO platform_core.ingest_source (
                    source_application_id, object_type, export_base_url,
                    interval_seconds, lookback_versions, page_limit, enabled,
                    transport_mode, push_protocol_version,
                    contract_validation_mode, allow_empty_full
                ) VALUES (
                    :source_application_id, :object_type, :export_base_url,
                    :interval_seconds, :lookback_versions, :page_limit, :enabled,
                    :transport_mode, :push_protocol_version,
                    :contract_validation_mode, :allow_empty_full
                )
                ON CONFLICT (source_application_id, object_type) DO UPDATE
                SET export_base_url = EXCLUDED.export_base_url,
                    interval_seconds = EXCLUDED.interval_seconds,
                    lookback_versions = EXCLUDED.lookback_versions,
                    page_limit = EXCLUDED.page_limit,
                    enabled = EXCLUDED.enabled,
                    transport_mode = EXCLUDED.transport_mode,
                    push_protocol_version = EXCLUDED.push_protocol_version,
                    contract_validation_mode = EXCLUDED.contract_validation_mode,
                    allow_empty_full = EXCLUDED.allow_empty_full,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "source_application_id": config.source_application_id,
                "object_type": config.object_type,
                "export_base_url": config.export_base_url,
                "interval_seconds": config.interval_seconds,
                "lookback_versions": config.lookback_versions,
                "page_limit": config.page_limit,
                "enabled": config.enabled,
                "transport_mode": config.transport_mode,
                "push_protocol_version": config.push_protocol_version,
                "contract_validation_mode": config.contract_validation_mode,
                "allow_empty_full": config.allow_empty_full,
            },
        )
        stored = await self.get_source(
            session,
            source_application_id=config.source_application_id,
            object_type=config.object_type,
        )
        if stored is None:  # pragma: no cover - defensive
            raise IngestConfigError("failed to persist ingest source")
        return stored

    async def _require_approved_certification(
        self, session: AsyncSession, config: IngestSourceConfig
    ) -> None:
        result = await session.execute(
            text(
                """
                SELECT c.certification_id
                FROM platform_core.ingest_contract_certification AS c
                JOIN platform_core.ingest_contract AS ic
                  ON ic.source_application_id = c.source_application_id
                 AND ic.object_type = c.object_type
                 AND ic.contract_version = c.contract_version
                 AND ic.schema_fingerprint = c.schema_fingerprint
                WHERE c.source_application_id = :source_application_id
                  AND c.object_type = :object_type
                  AND c.status = 'APPROVED'
                  AND c.transport_mode = :transport_mode
                  AND ic.status = 'ACTIVE'
                LIMIT 1
                """
            ),
            {
                "source_application_id": config.source_application_id,
                "object_type": config.object_type,
                "transport_mode": config.transport_mode,
            },
        )
        if result.one_or_none() is None:
            raise IngestEnforceNotCertifiedError(
                "enabled PUSH_AGENT and Pull ENFORCE require an APPROVED "
                "certification for the ACTIVE ingest contract and schema fingerprint"
            )

    async def seed_sources(
        self, session: AsyncSession, configs: list[IngestSourceConfig]
    ) -> int:
        """Insert sources that do not already exist; returns how many were added."""
        added = 0
        for config in configs:
            existing = await self.get_source(
                session,
                source_application_id=config.source_application_id,
                object_type=config.object_type,
            )
            if existing is None:
                await self.upsert_source(session, config)
                added += 1
        return added

    async def get_policy(self, session: AsyncSession) -> IngestPolicy:
        result = await session.execute(
            text(
                """
                SELECT retention_keep_versions, retention_keep_days,
                       payload_max_bytes, page_limit_default, page_limit_max,
                       scheduled_reconcile_enabled, reconcile_interval_hours,
                       push_staging_retention_hours, updated_at
                FROM platform_core.ingest_policy
                WHERE id = TRUE
                """
            )
        )
        row = result.one_or_none()
        if row is None:  # pragma: no cover - migration seeds the singleton row
            raise IngestConfigError("ingest policy row is missing")
        return IngestPolicy(
            retention_keep_versions=row.retention_keep_versions,
            retention_keep_days=row.retention_keep_days,
            payload_max_bytes=row.payload_max_bytes,
            page_limit_default=row.page_limit_default,
            page_limit_max=row.page_limit_max,
            scheduled_reconcile_enabled=row.scheduled_reconcile_enabled,
            reconcile_interval_hours=row.reconcile_interval_hours,
            push_staging_retention_hours=row.push_staging_retention_hours,
            updated_at=row.updated_at,
        )

    async def save_policy(self, session: AsyncSession, policy: IngestPolicy) -> IngestPolicy:
        if policy.page_limit_default > policy.page_limit_max:
            raise IngestConfigError("page_limit_default cannot exceed page_limit_max")
        if not 1 <= policy.push_staging_retention_hours <= 168:
            raise IngestConfigError(
                "push_staging_retention_hours must be between 1 and 168"
            )
        await session.execute(
            text(
                """
                UPDATE platform_core.ingest_policy
                SET retention_keep_versions = :retention_keep_versions,
                    retention_keep_days = :retention_keep_days,
                    payload_max_bytes = :payload_max_bytes,
                    page_limit_default = :page_limit_default,
                    page_limit_max = :page_limit_max,
                    scheduled_reconcile_enabled = :scheduled_reconcile_enabled,
                    reconcile_interval_hours = :reconcile_interval_hours,
                    push_staging_retention_hours = :push_staging_retention_hours,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = TRUE
                """
            ),
            {
                "retention_keep_versions": policy.retention_keep_versions,
                "retention_keep_days": policy.retention_keep_days,
                "payload_max_bytes": policy.payload_max_bytes,
                "page_limit_default": policy.page_limit_default,
                "page_limit_max": policy.page_limit_max,
                "scheduled_reconcile_enabled": policy.scheduled_reconcile_enabled,
                "reconcile_interval_hours": policy.reconcile_interval_hours,
                "push_staging_retention_hours": policy.push_staging_retention_hours,
            },
        )
        return await self.get_policy(session)
