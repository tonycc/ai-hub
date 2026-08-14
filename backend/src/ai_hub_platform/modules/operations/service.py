from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import quote

import httpx
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession


class OperationsService:
    @staticmethod
    def _metric_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float | str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    async def summary(
        self,
        session: AsyncSession,
        *,
        visible_application_ids: frozenset[str] | None,
        rabbitmq_management_url: str | None,
        rabbitmq_vhost: str,
        rabbitmq_username: str | None,
        rabbitmq_password: SecretStr | None,
        http_client: httpx.AsyncClient,
        event_backlog_warning: int,
        event_backlog_critical: int,
    ) -> dict[str, Any]:
        applications = await self._application_entries(session, visible_application_ids)
        projections = await self._projection_health(session, visible_application_ids)
        events = await self._event_health(
            session,
            rabbitmq_management_url=rabbitmq_management_url,
            rabbitmq_vhost=rabbitmq_vhost,
            rabbitmq_username=rabbitmq_username,
            rabbitmq_password=rabbitmq_password,
            http_client=http_client,
            backlog_warning=event_backlog_warning,
            backlog_critical=event_backlog_critical,
        )
        statuses = [
            item["status"] for group in (applications, projections, events) for item in group
        ]
        overall = (
            "DEGRADED"
            if any(status in {"WARNING", "CRITICAL"} for status in statuses)
            else "HEALTHY"
        )
        return {
            "observed_at": datetime.now(UTC),
            "overall_status": overall,
            "application_entries": applications,
            "event_queues": events,
            "projections": projections,
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

    async def _projection_health(
        self,
        session: AsyncSession,
        visible_application_ids: frozenset[str] | None,
    ) -> list[dict[str, Any]]:
        table_present = bool(
            await session.scalar(
                sa.text(
                    "SELECT to_regclass('platform_projection.projection_checkpoint') IS NOT NULL"
                )
            )
        )
        if not table_present:
            return []
        rows = (
            (
                await session.execute(
                    sa.text(
                        """
                    SELECT c.producer_application_id AS application_id,
                           a.name AS application_name,
                           c.last_source_sequence, c.last_snapshot_watermark,
                           c.updated_at,
                           COUNT(g.record_id) FILTER (WHERE g.status = 'OPEN')::integer
                               AS open_gap_count
                    FROM platform_projection.projection_checkpoint AS c
                    JOIN platform_core.application AS a
                      ON a.application_id = c.producer_application_id
                    LEFT JOIN platform_projection.projection_gap AS g
                      ON g.producer_application_id = c.producer_application_id
                    WHERE (
                        CAST(:visible_application_ids AS varchar[]) IS NULL
                        OR c.producer_application_id = ANY(
                            CAST(:visible_application_ids AS varchar[])
                        )
                    )
                    GROUP BY c.producer_application_id, a.name,
                             c.last_source_sequence, c.last_snapshot_watermark,
                             c.updated_at
                    ORDER BY a.name
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
        now = datetime.now(UTC)
        result: list[dict[str, Any]] = []
        for row in rows:
            age_seconds = max(0, int((now - row["updated_at"]).total_seconds()))
            status = "HEALTHY"
            reason = "Projection checkpoint is current and no version gap is open"
            if row["open_gap_count"]:
                status = "CRITICAL"
                reason = "Projection has an unresolved aggregate version gap"
            elif age_seconds > 900:
                status = "WARNING"
                reason = "Projection checkpoint has not advanced for more than 15 minutes"
            result.append(
                {
                    **dict(row),
                    "checkpoint_age_seconds": age_seconds,
                    "status": status,
                    "reason": reason,
                }
            )
        return result

    async def _event_health(
        self,
        session: AsyncSession,
        *,
        rabbitmq_management_url: str | None,
        rabbitmq_vhost: str,
        rabbitmq_username: str | None,
        rabbitmq_password: SecretStr | None,
        http_client: httpx.AsyncClient,
        backlog_warning: int,
        backlog_critical: int,
    ) -> list[dict[str, Any]]:
        event_table_present = bool(
            await session.scalar(
                sa.text(
                    "SELECT to_regclass("
                    "'platform_core.event_contract_registration'"
                    ") IS NOT NULL"
                )
            )
        )
        if not event_table_present:
            return []
        event_contracts_present = bool(
            await session.scalar(
                sa.text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM platform_core.event_contract_registration
                        WHERE status = 'ACTIVE'
                    )
                    """
                )
            )
        )
        if not event_contracts_present:
            return []
        if not rabbitmq_management_url or not rabbitmq_username or not rabbitmq_password:
            return [
                {
                    "queue_name": "event-observability",
                    "messages_ready": 0,
                    "messages_unacknowledged": 0,
                    "consumer_count": 0,
                    "status": "CRITICAL",
                    "reason": "RabbitMQ read-only observer is not configured",
                }
            ]
        url = f"{rabbitmq_management_url.rstrip('/')}/api/queues/{quote(rabbitmq_vhost, safe='')}"
        try:
            response = await http_client.get(
                url,
                auth=(rabbitmq_username, rabbitmq_password.get_secret_value()),
                timeout=3.0,
            )
            response.raise_for_status()
            payload = cast(object, response.json())
        except httpx.HTTPError, ValueError:
            return [
                {
                    "queue_name": "event-observability",
                    "messages_ready": 0,
                    "messages_unacknowledged": 0,
                    "consumer_count": 0,
                    "status": "CRITICAL",
                    "reason": "RabbitMQ read-only management endpoint is unavailable",
                }
            ]
        raw_rows = cast(list[object], payload) if isinstance(payload, list) else []
        rows: list[dict[str, Any]] = []
        for raw_value in raw_rows:
            if not isinstance(raw_value, dict):
                continue
            raw = cast(dict[str, object], raw_value)
            name = str(raw.get("name", ""))
            if not name.startswith("ai-hub.") or name.endswith(".dlq"):
                continue
            ready = self._metric_int(raw.get("messages_ready", 0))
            unacknowledged = self._metric_int(raw.get("messages_unacknowledged", 0))
            consumers = self._metric_int(raw.get("consumers", 0))
            status = "HEALTHY"
            reason = "Queue is drained and has an active consumer"
            if consumers == 0:
                status = "CRITICAL"
                reason = "No active consumer is attached to the queue"
            elif ready + unacknowledged > backlog_critical:
                status = "CRITICAL"
                reason = "Event backlog exceeds the critical threshold"
            elif ready + unacknowledged > backlog_warning:
                status = "WARNING"
                reason = "Event backlog exceeds the warning threshold"
            rows.append(
                {
                    "queue_name": name,
                    "messages_ready": ready,
                    "messages_unacknowledged": unacknowledged,
                    "consumer_count": consumers,
                    "status": status,
                    "reason": reason,
                }
            )
        return sorted(rows, key=lambda row: row["queue_name"])
