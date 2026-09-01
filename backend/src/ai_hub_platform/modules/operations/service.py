from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.ingest.config_store import IngestConfigStore
from ai_hub_platform.modules.ingest.sources import load_push_progress, load_sync_cursors

_SUCCESS_STATUSES = {"ok", "loaded", "COMPLETED"}
_IN_PROGRESS_STATUSES = {"OPEN", "RECEIVING", "COMPLETING"}
_FAILED_STATUSES = {"failed", "FAILED", "EXPIRED", "ABORTED"}


def diagnose_source_status(*, enabled: bool, last_status: str | None) -> tuple[str, str]:
    if not enabled:
        return "DISABLED", "Data source is disabled"
    if last_status is None:
        return "UNKNOWN", "No ingest execution has been observed"
    if last_status in _SUCCESS_STATUSES:
        return "HEALTHY", "Latest ingest execution completed successfully"
    if last_status in _IN_PROGRESS_STATUSES:
        return "HEALTHY", f"Push generation is currently {last_status}"
    if last_status in _FAILED_STATUSES:
        return "CRITICAL", f"Latest ingest execution reported {last_status}"
    return "WARNING", f"Latest ingest execution has unknown status {last_status}"


def diagnose_freshness(
    *,
    observed_at: datetime,
    enabled: bool,
    interval_seconds: int,
    last_success_at: datetime | None,
) -> tuple[str, str, int | None, datetime | None]:
    if not enabled:
        return "DISABLED", "Data source is disabled", None, None
    if last_success_at is None:
        return "WARNING", "No successful ingest has been observed", None, None
    age_seconds = max(0, int((observed_at - last_success_at).total_seconds()))
    next_expected_at = last_success_at + timedelta(seconds=interval_seconds)
    if age_seconds > interval_seconds * 4:
        return (
            "CRITICAL",
            "Last successful ingest is more than four configured intervals old",
            age_seconds,
            next_expected_at,
        )
    if age_seconds > interval_seconds * 2:
        return (
            "WARNING",
            "Last successful ingest is more than two configured intervals old",
            age_seconds,
            next_expected_at,
        )
    return (
        "HEALTHY",
        "Last successful ingest is within two configured intervals",
        age_seconds,
        next_expected_at,
    )


class OperationsService:
    async def summary(
        self,
        session: AsyncSession,
        *,
        raw_session: AsyncSession,
        visible_application_ids: frozenset[str] | None,
    ) -> dict[str, Any]:
        observed_at = datetime.now(UTC)
        applications = await self._application_entries(session, visible_application_ids)
        data_sources, freshness = await self._ingest_diagnostics(
            session,
            raw_session,
            observed_at=observed_at,
            visible_application_ids=visible_application_ids,
        )
        statuses = [
            item["status"]
            for group in (applications, data_sources, freshness)
            for item in group
        ]
        overall = (
            "DEGRADED"
            if any(status in {"WARNING", "CRITICAL"} for status in statuses)
            else "HEALTHY"
        )
        return {
            "observed_at": observed_at,
            "overall_status": overall,
            "application_entries": applications,
            "data_source_entries": data_sources,
            "sync_freshness_entries": freshness,
            "runbook_path": "/portal-api/v1/developer/assets/integration-guide",
        }

    async def _ingest_diagnostics(
        self,
        session: AsyncSession,
        raw_session: AsyncSession,
        *,
        observed_at: datetime,
        visible_application_ids: frozenset[str] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        source_rows = await IngestConfigStore().list_sources(session)
        if visible_application_ids is not None:
            source_rows = [
                row
                for row in source_rows
                if row.config.source_application_id in visible_application_ids
            ]
        source_ids = sorted(
            {row.config.source_application_id for row in source_rows}
        )
        names: dict[str, str] = {}
        if source_ids:
            name_rows = (
                await session.execute(
                    sa.text(
                        """
                        SELECT application_id, name
                        FROM platform_core.application
                        WHERE application_id = ANY(CAST(:source_ids AS varchar[]))
                        """
                    ),
                    {"source_ids": source_ids},
                )
            ).all()
            names = {str(row.application_id): str(row.name) for row in name_rows}

        pull_progress = await load_sync_cursors(raw_session)
        push_progress = await load_push_progress(raw_session)
        data_sources: list[dict[str, Any]] = []
        freshness: list[dict[str, Any]] = []
        for row in source_rows:
            config = row.config
            key = (config.source_application_id, config.object_type)
            progress = (
                push_progress.get(key)
                if config.transport_mode == "PUSH_AGENT"
                else pull_progress.get(key)
            ) or {}
            last_status = progress.get("last_status")
            source_status, source_reason = diagnose_source_status(
                enabled=config.enabled,
                last_status=last_status,
            )
            application_name = names.get(
                config.source_application_id,
                config.source_application_id,
            )
            data_sources.append(
                {
                    "source_application_id": config.source_application_id,
                    "application_name": application_name,
                    "object_type": config.object_type,
                    "transport_mode": config.transport_mode,
                    "enabled": config.enabled,
                    "interval_seconds": config.interval_seconds,
                    "last_cursor": progress.get("last_cursor"),
                    "last_sync_at": progress.get("last_sync_at"),
                    "last_success_at": progress.get("last_success_at"),
                    "last_status": last_status,
                    "status": source_status,
                    "reason": source_reason,
                }
            )
            freshness_status, freshness_reason, age_seconds, next_expected_at = (
                diagnose_freshness(
                    observed_at=observed_at,
                    enabled=config.enabled,
                    interval_seconds=config.interval_seconds,
                    last_success_at=progress.get("last_success_at"),
                )
            )
            freshness.append(
                {
                    "source_application_id": config.source_application_id,
                    "application_name": application_name,
                    "object_type": config.object_type,
                    "expected_interval_seconds": config.interval_seconds,
                    "last_success_at": progress.get("last_success_at"),
                    "next_expected_at": next_expected_at,
                    "age_seconds": age_seconds,
                    "status": freshness_status,
                    "reason": freshness_reason,
                }
            )
        return data_sources, freshness

    async def _application_entries(
        self,
        session: AsyncSession,
        visible_application_ids: frozenset[str] | None,
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT a.application_id, a.name AS application_name,
                           e.environment, e.portal_url, e.health_url,
                           e.last_health_status, e.last_health_checked_at,
                           e.status AS environment_status
                    FROM platform_core.application AS a
                    JOIN platform_core.application_environment AS e
                      ON e.application_id = a.application_id
                    WHERE (CAST(:visible_application_ids AS varchar[]) IS NULL
                           OR a.application_id = ANY(CAST(:visible_application_ids AS varchar[])))
                    ORDER BY a.name, e.environment
                    """
                    ),
                    {
                        "visible_application_ids": (
                            sorted(visible_application_ids)
                            if visible_application_ids is not None
                            else None
                        )
                    },
                )
            )
            .mappings()
            .all()
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            health = row["last_health_status"]
            status = "UNKNOWN"
            reason = "No health check result has been reported"
            if row["environment_status"] != "ACTIVE":
                status = "DISABLED"
                reason = "Application environment is disabled"
            elif health == "HEALTHY":
                status = "HEALTHY"
                reason = "Latest application entry health check passed"
            elif health:
                status = "CRITICAL"
                reason = f"Latest application entry check reported {health}"
            result.append(
                {
                    **dict(row),
                    "status": status,
                    "reason": reason,
                }
            )
        return result
