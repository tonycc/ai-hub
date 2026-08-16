from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from standalone_app.export import (
    EXAMPLE_RECORD_OBJECT_TYPE,
    append_ingest_change,
    as_object_id,
    example_record_payload,
)


@dataclass(frozen=True, slots=True)
class RecordMutation:
    record_id: UUID
    name: str
    state: str
    owner_subject: str
    aggregate_version: int
    updated_at: datetime


def _mutation_from_row(row: sa.RowMapping) -> RecordMutation:
    return RecordMutation(
        record_id=row["id"],
        name=row["name"],
        state=row["state"],
        owner_subject=row["owner_subject"],
        aggregate_version=row["aggregate_version"],
        updated_at=row["updated_at"],
    )


async def change_record(
    session: AsyncSession,
    *,
    data_ingest_enabled: bool,
    record_id: UUID,
    owner_subject: str,
    name: str,
) -> RecordMutation | None:
    """Change a record and optionally append an ingest change-log entry."""

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
    if data_ingest_enabled:
        await append_ingest_change(
            session,
            object_type=EXAMPLE_RECORD_OBJECT_TYPE,
            object_id=as_object_id(mutation.record_id),
            operation="upsert",
            payload=example_record_payload(
                name=mutation.name,
                state=mutation.state,
                owner_subject=mutation.owner_subject,
            ),
        )
    return mutation


async def delete_record(
    session: AsyncSession,
    *,
    data_ingest_enabled: bool,
    record_id: UUID,
    owner_subject: str,
) -> RecordMutation | None:
    """Soft-delete a record and optionally append an ingest delete entry."""

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
    if data_ingest_enabled:
        await append_ingest_change(
            session,
            object_type=EXAMPLE_RECORD_OBJECT_TYPE,
            object_id=as_object_id(mutation.record_id),
            operation="delete",
            payload=None,
        )
    return mutation
