"""Incremental export for data aggregation (M7)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from ai_hub_sdk import (
    ExportPage,
    ExportRecord,
    PayloadContract,
    build_export_page,
)
from sqlalchemy.ext.asyncio import AsyncSession

EXAMPLE_RECORD_OBJECT_TYPE = "example_record"
EXAMPLE_RECORD_CONTRACT = PayloadContract(
    object_type=EXAMPLE_RECORD_OBJECT_TYPE,
    contract_version="example_record.v1",
    allowed_keys=frozenset({"name", "state", "owner_subject"}),
)


def example_record_payload(
    *,
    name: str,
    state: str,
    owner_subject: str,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "state": state,
        "owner_subject": owner_subject,
    }
    EXAMPLE_RECORD_CONTRACT.validate_payload(payload)
    return payload


async def append_ingest_change(
    session: AsyncSession,
    *,
    object_type: str,
    object_id: str,
    operation: str,
    payload: dict[str, Any] | None,
) -> int:
    """Allocate a commit-time global version and append one change-log row."""
    if operation == "upsert":
        EXAMPLE_RECORD_CONTRACT.validate_payload(payload)
    version = int(
        (
            await session.execute(
                sa.text(
                    """
                    INSERT INTO app.ingest_version_counter (object_type, next_version)
                    VALUES (:object_type, 2)
                    ON CONFLICT (object_type) DO UPDATE
                    SET next_version = app.ingest_version_counter.next_version + 1
                    RETURNING next_version - 1
                    """
                ),
                {"object_type": object_type},
            )
        ).scalar_one()
    )
    await session.execute(
        sa.text(
            """
            INSERT INTO app.ingest_change_log (
                object_type, object_id, operation, version, payload
            ) VALUES (
                :object_type, :object_id, :operation, :version,
                CAST(:payload AS jsonb)
            )
            ON CONFLICT (object_type, object_id, version) DO NOTHING
            """
        ),
        {
            "object_type": object_type,
            "object_id": object_id,
            "operation": operation,
            "version": version,
            "payload": None if payload is None else json.dumps(payload, ensure_ascii=True),
        },
    )
    return version


async def export_example_records(
    session: AsyncSession,
    *,
    since_version: int,
    limit: int,
) -> ExportPage:
    if since_version < 0:
        raise ValueError("since_version must be >= 0")
    if limit < 1:
        raise ValueError("limit must be >= 1")
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT object_id, operation, version, payload
                FROM app.ingest_change_log
                WHERE object_type = :object_type
                  AND version > :since_version
                ORDER BY version ASC
                LIMIT :limit
                """
            ),
            {
                "object_type": EXAMPLE_RECORD_OBJECT_TYPE,
                "since_version": since_version,
                "limit": limit + 1,
            },
        )
    ).mappings().all()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    records = [
        ExportRecord(
            object_id=str(row["object_id"]),
            operation=row["operation"],
            version=int(row["version"]),
            payload=None if row["payload"] is None else dict(row["payload"]),
        )
        for row in page_rows
    ]
    counter = (
        await session.execute(
            sa.text(
                """
                SELECT next_version - 1
                FROM app.ingest_version_counter
                WHERE object_type = :object_type
                """
            ),
            {"object_type": EXAMPLE_RECORD_OBJECT_TYPE},
        )
    ).scalar_one_or_none()
    stream_hw = int(counter) if counter is not None else 0
    high_watermark = max(
        stream_hw,
        max((record.version for record in records), default=since_version),
    )
    return build_export_page(
        object_type=EXAMPLE_RECORD_OBJECT_TYPE,
        payload_contract_version=EXAMPLE_RECORD_CONTRACT.contract_version,
        records=records,
        high_watermark=high_watermark,
        has_more=has_more,
    )


async def seed_ingest_baseline_if_empty(session: AsyncSession) -> None:
    """Publish current ACTIVE records once so empty logs still export a baseline."""
    existing = (
        await session.execute(
            sa.text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM app.ingest_change_log
                    WHERE object_type = :object_type
                )
                """
            ),
            {"object_type": EXAMPLE_RECORD_OBJECT_TYPE},
        )
    ).scalar_one()
    if existing:
        return
    rows = (
        await session.execute(
            sa.text(
                """
                SELECT id, name, state, owner_subject
                FROM app.example_record
                WHERE state = 'ACTIVE'
                ORDER BY id
                """
            )
        )
    ).mappings().all()
    for row in rows:
        await append_ingest_change(
            session,
            object_type=EXAMPLE_RECORD_OBJECT_TYPE,
            object_id=str(row["id"]),
            operation="upsert",
            payload=example_record_payload(
                name=str(row["name"]),
                state=str(row["state"]),
                owner_subject=str(row["owner_subject"]),
            ),
        )


def as_object_id(record_id: UUID) -> str:
    return str(record_id)
