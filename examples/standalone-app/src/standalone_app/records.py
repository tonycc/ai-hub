from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from ai_hub_sdk import CloudEvent
from sqlalchemy.ext.asyncio import AsyncSession

from standalone_app.events import (
    EVENT_TYPE_CHANGED,
    EVENT_TYPE_DELETED,
    append_record_event,
)


@dataclass(frozen=True, slots=True)
class RecordMutation:
    record_id: UUID
    name: str
    state: str
    owner_subject: str
    aggregate_version: int
    updated_at: datetime
    event: CloudEvent | None


def _mutation_from_row(
    row: sa.RowMapping, event: CloudEvent | None = None
) -> RecordMutation:
    return RecordMutation(
        record_id=row["id"],
        name=row["name"],
        state=row["state"],
        owner_subject=row["owner_subject"],
        aggregate_version=row["aggregate_version"],
        updated_at=row["updated_at"],
        event=event,
    )


async def change_record(
    session: AsyncSession,
    *,
    application_id: str,
    events_enabled: bool,
    record_id: UUID,
    owner_subject: str,
    name: str,
    actor_type: Literal["user", "service", "system"],
    actor_id: str,
    trace_id: str | None,
) -> RecordMutation | None:
    """Change a record and append its event in the caller-owned transaction."""

    row = (
        await session.execute(
            sa.text(
                """
                UPDATE app.example_record
                SET name = :name,
                    aggregate_version = aggregate_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :record_id AND owner_subject = :owner_subject
                  AND state = 'ACTIVE'
                RETURNING id, name, state, owner_subject,
                          aggregate_version, updated_at
                """
            ),
            {
                "record_id": record_id,
                "name": name,
                "owner_subject": owner_subject,
            },
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    mutation = _mutation_from_row(row)
    if events_enabled:
        event = await append_record_event(
            session,
            application_id=application_id,
            event_type=EVENT_TYPE_CHANGED,
            record_id=mutation.record_id,
            name=mutation.name,
            state=mutation.state,
            owner_subject=mutation.owner_subject,
            aggregate_version=mutation.aggregate_version,
            actor_type=actor_type,
            actor_id=actor_id,
            trace_id=trace_id,
            occurred_at=mutation.updated_at,
        )
        mutation = _mutation_from_row(row, event)
    return mutation


async def delete_record(
    session: AsyncSession,
    *,
    application_id: str,
    events_enabled: bool,
    record_id: UUID,
    owner_subject: str,
    actor_type: Literal["user", "service", "system"],
    actor_id: str,
    trace_id: str | None,
) -> RecordMutation | None:
    """Soft-delete a record and append its tombstone in one transaction."""

    row = (
        await session.execute(
            sa.text(
                """
                UPDATE app.example_record
                SET state = 'DELETED',
                    aggregate_version = aggregate_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :record_id AND owner_subject = :owner_subject
                  AND state = 'ACTIVE'
                RETURNING id, name, state, owner_subject,
                          aggregate_version, updated_at
                """
            ),
            {"record_id": record_id, "owner_subject": owner_subject},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    mutation = _mutation_from_row(row)
    if events_enabled:
        event = await append_record_event(
            session,
            application_id=application_id,
            event_type=EVENT_TYPE_DELETED,
            record_id=mutation.record_id,
            name=mutation.name,
            state=mutation.state,
            owner_subject=mutation.owner_subject,
            aggregate_version=mutation.aggregate_version,
            actor_type=actor_type,
            actor_id=actor_id,
            trace_id=trace_id,
            occurred_at=mutation.updated_at,
        )
        mutation = _mutation_from_row(row, event)
    return mutation
