"""Authoritative ingest configuration stored in platform_core (design §2.5.1).

Sources and the global policy are portal-managed. Readers include the portal API
(ai_hub_platform) and the ingest scheduler / one-shot CLIs (ai_hub_raw). The
operations JSON document remains only a bootstrap seed for fresh environments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.ingest.sources import IngestSourceConfig


class IngestConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IngestPolicy:
    retention_keep_versions: int
    retention_keep_days: int | None
    payload_max_bytes: int
    page_limit_default: int
    page_limit_max: int
    scheduled_reconcile_enabled: bool
    reconcile_interval_hours: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class IngestSourceRow:
    config: IngestSourceConfig
    updated_at: datetime


def _row_to_source(row: Any) -> IngestSourceRow:
    return IngestSourceRow(
        config=IngestSourceConfig(
            source_application_id=str(row.source_application_id),
            object_type=str(row.object_type),
            export_base_url=str(row.export_base_url),
            interval_seconds=int(row.interval_seconds),
            lookback_versions=int(row.lookback_versions),
            page_limit=int(row.page_limit),
            enabled=bool(row.enabled),
        ),
        updated_at=row.updated_at,
    )


class IngestConfigStore:
    """CRUD over platform_core ingest configuration within the caller's transaction."""

    async def list_sources(self, session: AsyncSession) -> list[IngestSourceRow]:
        result = await session.execute(
            text(
                """
                SELECT source_application_id, object_type, export_base_url,
                       interval_seconds, lookback_versions, page_limit, enabled,
                       updated_at
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
                """
                SELECT source_application_id, object_type, export_base_url,
                       interval_seconds, lookback_versions, page_limit, enabled,
                       updated_at
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
        await session.execute(
            text(
                """
                INSERT INTO platform_core.ingest_source (
                    source_application_id, object_type, export_base_url,
                    interval_seconds, lookback_versions, page_limit, enabled
                ) VALUES (
                    :source_application_id, :object_type, :export_base_url,
                    :interval_seconds, :lookback_versions, :page_limit, :enabled
                )
                ON CONFLICT (source_application_id, object_type) DO UPDATE
                SET export_base_url = EXCLUDED.export_base_url,
                    interval_seconds = EXCLUDED.interval_seconds,
                    lookback_versions = EXCLUDED.lookback_versions,
                    page_limit = EXCLUDED.page_limit,
                    enabled = EXCLUDED.enabled,
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
                       updated_at
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
            updated_at=row.updated_at,
        )

    async def save_policy(self, session: AsyncSession, policy: IngestPolicy) -> IngestPolicy:
        if policy.page_limit_default > policy.page_limit_max:
            raise IngestConfigError("page_limit_default cannot exceed page_limit_max")
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
            },
        )
        return await self.get_policy(session)
