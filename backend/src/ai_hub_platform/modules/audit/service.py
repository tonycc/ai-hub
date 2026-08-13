from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.shared.database import Database


@dataclass(frozen=True, slots=True)
class AuditRecord:
    request_id: str
    action: str
    result: str
    actor_type: str = "anonymous"
    actor_id: str | None = None
    application_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    error_code: str | None = None
    authorization_version: int | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=lambda: {})
    audit_id: UUID = field(default_factory=uuid4)


class AuditService:
    async def append(self, session: AsyncSession, record: AuditRecord) -> None:
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_core.audit_event
                    (audit_id, occurred_at, request_id, trace_id, application_id,
                     actor_type, actor_id, action, target_type, target_id, result,
                     error_code, authorization_version, metadata)
                VALUES
                    (:audit_id, :occurred_at, :request_id, :trace_id, :application_id,
                     :actor_type, :actor_id, :action, :target_type, :target_id, :result,
                     :error_code, :authorization_version, CAST(:metadata AS jsonb))
                """
            ),
            {
                "audit_id": record.audit_id,
                "occurred_at": datetime.now(UTC),
                "request_id": record.request_id,
                "trace_id": record.trace_id,
                "application_id": record.application_id,
                "actor_type": record.actor_type,
                "actor_id": record.actor_id,
                "action": record.action,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "result": record.result,
                "error_code": record.error_code,
                "authorization_version": record.authorization_version,
                "metadata": json.dumps(record.metadata),
            },
        )

    async def append_committed(self, database: Database, record: AuditRecord) -> None:
        """Persist a security outcome independently of the request transaction."""
        async with database.session_factory() as session:
            try:
                await self.append(session, record)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def query(
        self,
        session: AsyncSession,
        *,
        application_ids: frozenset[str] | None,
        actor_id: str | None,
        action: str | None,
        result: str | None,
        request_id: str | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        parameters = {
            "application_ids": (sorted(application_ids) if application_ids is not None else None),
            "actor_id": actor_id,
            "action": action,
            "result": result,
            "request_id": request_id,
            "occurred_from": occurred_from,
            "occurred_to": occurred_to,
            "limit": limit,
            "offset": offset,
        }
        where_clause = """
            (CAST(:application_ids AS varchar[]) IS NULL
             OR application_id = ANY(CAST(:application_ids AS varchar[])))
            AND (CAST(:actor_id AS varchar) IS NULL OR actor_id = :actor_id)
            AND (CAST(:action AS varchar) IS NULL OR action = :action)
            AND (CAST(:result AS varchar) IS NULL OR result = :result)
            AND (CAST(:request_id AS varchar) IS NULL OR request_id = :request_id)
            AND (CAST(:occurred_from AS timestamptz) IS NULL
                 OR occurred_at >= :occurred_from)
            AND (CAST(:occurred_to AS timestamptz) IS NULL
                 OR occurred_at < :occurred_to)
        """
        total = await session.scalar(
            sa.text(
                f"""
                SELECT COUNT(*) FROM platform_core.audit_event
                WHERE {where_clause}
                """  # noqa: S608 - static SQL fragment
            ),
            parameters,
        )
        rows = (
            (
                await session.execute(
                    sa.text(
                        f"""
                    SELECT audit_id, occurred_at, request_id, trace_id,
                           application_id, actor_type, actor_id, action,
                           target_type, target_id, result, error_code,
                           authorization_version, metadata
                    FROM platform_core.audit_event
                    WHERE {where_clause}
                    ORDER BY occurred_at DESC, audit_id DESC
                    LIMIT :limit OFFSET :offset
                    """  # noqa: S608 - static SQL fragment
                    ),
                    parameters,
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows], int(total or 0)
