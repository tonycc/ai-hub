from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


class OperationsService:
    async def summary(
        self,
        session: AsyncSession,
        *,
        visible_application_ids: frozenset[str] | None,
    ) -> dict[str, Any]:
        applications = await self._application_entries(session, visible_application_ids)
        statuses = [item["status"] for item in applications]
        overall = (
            "DEGRADED"
            if any(status in {"WARNING", "CRITICAL"} for status in statuses)
            else "HEALTHY"
        )
        return {
            "observed_at": datetime.now(UTC),
            "overall_status": overall,
            "application_entries": applications,
            "runbook_path": "/portal-api/v1/developer/assets/integration-guide",
        }

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
