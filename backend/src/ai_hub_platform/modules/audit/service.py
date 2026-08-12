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
