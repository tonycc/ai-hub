from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from ai_hub_sdk import (
    CloudEvent,
    ExampleRecordSnapshot,
    example_record_snapshot_checksum,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

CONSUMER_ID = "ai-hub-platform-example-record-projection-v1"


class ProjectionContractError(ValueError):
    """The event is permanently incompatible with the registered projection."""


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    result: Literal["APPLIED", "DUPLICATE", "STALE", "GAP", "SNAPSHOT_COVERED"]
    event_id: UUID
    aggregate_version: int


def _payload_hash(event: CloudEvent) -> str:
    return hashlib.sha256(
        event.model_dump_json(exclude_none=False).encode()
    ).hexdigest()


def _record_id(event: CloudEvent) -> UUID:
    try:
        record_id = UUID(str(event.data["record_id"]))
    except (KeyError, TypeError, ValueError) as error:
        raise ProjectionContractError("event data must contain a UUID record_id") from error
    expected_subject = f"example-record/{record_id}"
    if event.subject != expected_subject:
        raise ProjectionContractError("event subject does not match data.record_id")
    return record_id


def _changed_values(event: CloudEvent) -> tuple[str, str, str]:
    try:
        name = str(event.data["name"])
        state = str(event.data["state"])
        owner_subject = str(event.data["owner_subject"])
    except KeyError as error:
        raise ProjectionContractError("changed event is missing a projection field") from error
    if not name or not state or not owner_subject:
        raise ProjectionContractError("changed event contains an empty projection field")
    return name, state, owner_subject


def validate_registered_event(event: CloudEvent) -> None:
    if event.producer_application_id != "standalone-example":
        raise ProjectionContractError("producer application is not registered for this queue")
    if event.object_type != "example_record" or event.event_version != 1:
        raise ProjectionContractError("object or event version is not supported")
    if event.type not in {
        "company.example.record.changed.v1",
        "company.example.record.deleted.v1",
    }:
        raise ProjectionContractError("event type is not registered")
    _record_id(event)
    if event.type.endswith("changed.v1"):
        _changed_values(event)


class ProjectionService:
    async def process(
        self,
        sessions: async_sessionmaker[AsyncSession],
        event: CloudEvent,
    ) -> ProjectionResult:
        validate_registered_event(event)
        async with sessions.begin() as session:
            inserted = (
                await session.execute(
                    sa.text(
                        """
                        INSERT INTO platform_projection.integration_inbox
                            (consumer_id, event_id, payload_hash,
                             producer_application_id, source_sequence, event_type)
                        VALUES
                            (:consumer_id, :event_id, :payload_hash,
                             :producer_application_id, :source_sequence, :event_type)
                        ON CONFLICT (consumer_id, event_id) DO NOTHING
                        RETURNING event_id
                        """
                    ),
                    {
                        "consumer_id": CONSUMER_ID,
                        "event_id": event.id,
                        "payload_hash": _payload_hash(event),
                        "producer_application_id": event.producer_application_id,
                        "source_sequence": event.source_sequence,
                        "event_type": event.type,
                    },
                )
            ).scalar_one_or_none()
            if inserted is None:
                existing_hash = (
                    await session.execute(
                        sa.text(
                            """
                            SELECT payload_hash
                            FROM platform_projection.integration_inbox
                            WHERE consumer_id = :consumer_id AND event_id = :event_id
                            """
                        ),
                        {"consumer_id": CONSUMER_ID, "event_id": event.id},
                    )
                ).scalar_one()
                if str(existing_hash) != _payload_hash(event):
                    raise ProjectionContractError(
                        "event_id was reused with a different payload"
                    )
                return ProjectionResult(
                    result="DUPLICATE",
                    event_id=event.id,
                    aggregate_version=event.aggregate_version,
                )
            await self._lock_checkpoint(session, event.producer_application_id)
            snapshot_watermark = int(
                (
                    await session.execute(
                        sa.text(
                            """
                            SELECT last_snapshot_watermark
                            FROM platform_projection.projection_checkpoint
                            WHERE producer_application_id = :application_id
                            """
                        ),
                        {"application_id": event.producer_application_id},
                    )
                ).scalar_one()
            )
            if event.source_sequence <= snapshot_watermark:
                await self._finish_inbox(session, event, "snapshot-covered")
                return ProjectionResult(
                    result="SNAPSHOT_COVERED",
                    event_id=event.id,
                    aggregate_version=event.aggregate_version,
                )
            result = await self._apply_or_buffer(session, event)
            await session.execute(
                sa.text(
                    """
                    UPDATE platform_projection.projection_checkpoint
                    SET last_source_sequence = GREATEST(last_source_sequence, :source_sequence),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE producer_application_id = :application_id
                    """
                ),
                {
                    "source_sequence": event.source_sequence,
                    "application_id": event.producer_application_id,
                },
            )
            await self._finish_inbox(session, event, result.lower())
            return ProjectionResult(
                result=result,
                event_id=event.id,
                aggregate_version=event.aggregate_version,
            )

    async def _lock_checkpoint(self, session: AsyncSession, application_id: str) -> None:
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_projection.projection_checkpoint
                    (producer_application_id, last_source_sequence, last_snapshot_watermark)
                VALUES (:application_id, 0, 0)
                ON CONFLICT (producer_application_id) DO NOTHING
                """
            ),
            {"application_id": application_id},
        )
        await session.execute(
            sa.text(
                """
                SELECT producer_application_id
                FROM platform_projection.projection_checkpoint
                WHERE producer_application_id = :application_id
                FOR UPDATE
                """
            ),
            {"application_id": application_id},
        )

    async def _current_version(
        self, session: AsyncSession, event: CloudEvent, record_id: UUID
    ) -> int:
        current = (
            await session.execute(
                sa.text(
                    """
                    SELECT aggregate_version
                    FROM platform_projection.example_record_projection
                    WHERE producer_application_id = :application_id
                      AND record_id = :record_id
                    FOR UPDATE
                    """
                ),
                {
                    "application_id": event.producer_application_id,
                    "record_id": record_id,
                },
            )
        ).scalar_one_or_none()
        return int(current or 0)

    async def _apply_or_buffer(
        self, session: AsyncSession, event: CloudEvent
    ) -> Literal["APPLIED", "STALE", "GAP"]:
        record_id = _record_id(event)
        current_version = await self._current_version(session, event, record_id)
        if event.aggregate_version <= current_version:
            return "STALE"
        if event.aggregate_version > current_version + 1:
            await self._buffer_gap(session, event, record_id, current_version + 1)
            return "GAP"
        await self._apply_event(session, event, record_id)
        await self._drain_pending(session, event.producer_application_id, record_id)
        return "APPLIED"

    async def _apply_event(
        self, session: AsyncSession, event: CloudEvent, record_id: UUID
    ) -> None:
        if event.type.endswith("changed.v1"):
            name, state, owner_subject = _changed_values(event)
            deleted_at = None
        else:
            existing = (
                await session.execute(
                    sa.text(
                        """
                        SELECT name, state, owner_subject
                        FROM platform_projection.example_record_projection
                        WHERE producer_application_id = :application_id
                          AND record_id = :record_id
                        """
                    ),
                    {
                        "application_id": event.producer_application_id,
                        "record_id": record_id,
                    },
                )
            ).mappings().one_or_none()
            if existing is None:
                raise ProjectionContractError(
                    "a delete event cannot create a projection without prior state"
                )
            name = str(existing["name"])
            state = str(existing["state"])
            owner_subject = str(existing["owner_subject"])
            deleted_at = event.time
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_projection.example_record_projection
                    (producer_application_id, record_id, name, state, owner_subject,
                     aggregate_version, source_sequence, source_occurred_at, deleted_at)
                VALUES
                    (:application_id, :record_id, :name, :state, :owner_subject,
                     :aggregate_version, :source_sequence, :source_occurred_at, :deleted_at)
                ON CONFLICT (producer_application_id, record_id) DO UPDATE
                SET name = EXCLUDED.name,
                    state = EXCLUDED.state,
                    owner_subject = EXCLUDED.owner_subject,
                    aggregate_version = EXCLUDED.aggregate_version,
                    source_sequence = EXCLUDED.source_sequence,
                    source_occurred_at = EXCLUDED.source_occurred_at,
                    deleted_at = EXCLUDED.deleted_at,
                    projected_at = CURRENT_TIMESTAMP
                WHERE platform_projection.example_record_projection.aggregate_version
                      < EXCLUDED.aggregate_version
                """
            ),
            {
                "application_id": event.producer_application_id,
                "record_id": record_id,
                "name": name,
                "state": state,
                "owner_subject": owner_subject,
                "aggregate_version": event.aggregate_version,
                "source_sequence": event.source_sequence,
                "source_occurred_at": event.time,
                "deleted_at": deleted_at,
            },
        )

    async def _buffer_gap(
        self,
        session: AsyncSession,
        event: CloudEvent,
        record_id: UUID,
        expected_version: int,
    ) -> None:
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_projection.projection_pending_event
                    (producer_application_id, record_id, aggregate_version, event_id,
                     source_sequence, event_payload)
                VALUES
                    (:application_id, :record_id, :aggregate_version, :event_id,
                     :source_sequence, CAST(:event_payload AS jsonb))
                ON CONFLICT (producer_application_id, record_id, aggregate_version)
                DO NOTHING
                """
            ),
            {
                "application_id": event.producer_application_id,
                "record_id": record_id,
                "aggregate_version": event.aggregate_version,
                "event_id": event.id,
                "source_sequence": event.source_sequence,
                "event_payload": event.model_dump_json(exclude_none=False),
            },
        )
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_projection.projection_gap
                    (producer_application_id, record_id, expected_version,
                     received_version, status)
                VALUES
                    (:application_id, :record_id, :expected_version,
                     :received_version, 'OPEN')
                ON CONFLICT (producer_application_id, record_id) DO UPDATE
                SET expected_version = LEAST(
                        platform_projection.projection_gap.expected_version,
                        EXCLUDED.expected_version
                    ),
                    received_version = GREATEST(
                        platform_projection.projection_gap.received_version,
                        EXCLUDED.received_version
                    ),
                    status = 'OPEN', resolved_at = NULL
                """
            ),
            {
                "application_id": event.producer_application_id,
                "record_id": record_id,
                "expected_version": expected_version,
                "received_version": event.aggregate_version,
            },
        )

    async def _drain_pending(
        self, session: AsyncSession, application_id: str, record_id: UUID
    ) -> None:
        while True:
            current_version = int(
                (
                    await session.execute(
                        sa.text(
                            """
                            SELECT aggregate_version
                            FROM platform_projection.example_record_projection
                            WHERE producer_application_id = :application_id
                              AND record_id = :record_id
                            """
                        ),
                        {"application_id": application_id, "record_id": record_id},
                    )
                ).scalar_one()
            )
            pending = (
                await session.execute(
                    sa.text(
                        """
                        DELETE FROM platform_projection.projection_pending_event
                        WHERE producer_application_id = :application_id
                          AND record_id = :record_id
                          AND aggregate_version = :next_version
                        RETURNING event_payload
                        """
                    ),
                    {
                        "application_id": application_id,
                        "record_id": record_id,
                        "next_version": current_version + 1,
                    },
                )
            ).scalar_one_or_none()
            if pending is None:
                remaining = (
                    await session.execute(
                        sa.text(
                            """
                            SELECT min(aggregate_version)
                            FROM platform_projection.projection_pending_event
                            WHERE producer_application_id = :application_id
                              AND record_id = :record_id
                            """
                        ),
                        {"application_id": application_id, "record_id": record_id},
                    )
                ).scalar_one_or_none()
                if remaining is None:
                    await session.execute(
                        sa.text(
                            """
                            UPDATE platform_projection.projection_gap
                            SET status = 'RESOLVED', resolved_at = CURRENT_TIMESTAMP
                            WHERE producer_application_id = :application_id
                              AND record_id = :record_id AND status = 'OPEN'
                            """
                        ),
                        {"application_id": application_id, "record_id": record_id},
                    )
                else:
                    await session.execute(
                        sa.text(
                            """
                            UPDATE platform_projection.projection_gap
                            SET expected_version = :expected_version,
                                received_version = :received_version
                            WHERE producer_application_id = :application_id
                              AND record_id = :record_id
                            """
                        ),
                        {
                            "application_id": application_id,
                            "record_id": record_id,
                            "expected_version": current_version + 1,
                            "received_version": int(remaining),
                        },
                    )
                return
            pending_event = CloudEvent.model_validate(pending)
            await self._apply_event(session, pending_event, record_id)
            await self._finish_inbox(
                session, pending_event, "applied-after-gap"
            )

    async def _finish_inbox(
        self, session: AsyncSession, event: CloudEvent, summary: str
    ) -> None:
        await session.execute(
            sa.text(
                """
                UPDATE platform_projection.integration_inbox
                SET processed_at = CURRENT_TIMESTAMP, result_summary = :summary
                WHERE consumer_id = :consumer_id AND event_id = :event_id
                """
            ),
            {"summary": summary, "consumer_id": CONSUMER_ID, "event_id": event.id},
        )


def validate_snapshot(snapshot: ExampleRecordSnapshot) -> None:
    if snapshot.producer_application_id != "standalone-example":
        raise ProjectionContractError("snapshot producer application is not registered")
    record_ids = [record.record_id for record in snapshot.records]
    if len(record_ids) != len(set(record_ids)):
        raise ProjectionContractError("snapshot contains duplicate record identifiers")
    expected = example_record_snapshot_checksum(
        snapshot.records,
        producer_application_id=snapshot.producer_application_id,
        watermark=snapshot.watermark,
    )
    if snapshot.checksum != expected:
        raise ProjectionContractError("snapshot checksum does not match its records")


async def rebuild_from_snapshot(
    sessions: async_sessionmaker[AsyncSession], snapshot: ExampleRecordSnapshot
) -> None:
    validate_snapshot(snapshot)
    async with sessions.begin() as session:
        application_id = snapshot.producer_application_id
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_projection.projection_checkpoint
                    (producer_application_id, last_source_sequence,
                     last_snapshot_watermark)
                VALUES (:application_id, 0, 0)
                ON CONFLICT (producer_application_id) DO NOTHING
                """
            ),
            {"application_id": application_id},
        )
        checkpoint = (
            await session.execute(
                sa.text(
                    """
                    SELECT last_source_sequence, last_snapshot_watermark
                    FROM platform_projection.projection_checkpoint
                    WHERE producer_application_id = :application_id
                    FOR UPDATE
                    """
                ),
                {"application_id": application_id},
            )
        ).mappings().one()
        installed_watermark = max(
            int(checkpoint["last_source_sequence"]),
            int(checkpoint["last_snapshot_watermark"]),
        )
        if installed_watermark > snapshot.watermark:
            raise ProjectionContractError(
                "snapshot watermark is older than the installed projection checkpoint"
            )
        for table in (
            "projection_pending_event",
            "projection_gap",
            "example_record_projection",
        ):
            await session.execute(
                sa.text(
                    f"DELETE FROM platform_projection.{table} "  # noqa: S608
                    "WHERE producer_application_id = :application_id"
                ),
                {"application_id": application_id},
            )
        await session.execute(
            sa.text(
                """
                DELETE FROM platform_projection.integration_inbox
                WHERE producer_application_id = :application_id
                """
            ),
            {"application_id": application_id},
        )
        for record in snapshot.records:
            await session.execute(
                sa.text(
                    """
                    INSERT INTO platform_projection.example_record_projection
                        (producer_application_id, record_id, name, state, owner_subject,
                         aggregate_version, source_sequence, source_occurred_at)
                    VALUES
                        (:application_id, :record_id, :name, :state, :owner_subject,
                         :aggregate_version, :source_sequence, :source_occurred_at)
                    """
                ),
                {
                    "application_id": application_id,
                    "record_id": record.record_id,
                    "name": record.name,
                    "state": record.state,
                    "owner_subject": record.owner_subject,
                    "aggregate_version": record.aggregate_version,
                    "source_sequence": snapshot.watermark,
                    "source_occurred_at": record.updated_at,
                },
            )
        await session.execute(
            sa.text(
                """
                INSERT INTO platform_projection.projection_checkpoint
                    (producer_application_id, last_source_sequence,
                     last_snapshot_watermark, last_snapshot_id, last_snapshot_at)
                VALUES
                    (:application_id, :watermark, :watermark,
                     :snapshot_id, :snapshot_at)
                ON CONFLICT (producer_application_id) DO UPDATE
                SET last_source_sequence = EXCLUDED.last_source_sequence,
                    last_snapshot_watermark = EXCLUDED.last_snapshot_watermark,
                    last_snapshot_id = EXCLUDED.last_snapshot_id,
                    last_snapshot_at = EXCLUDED.last_snapshot_at,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "application_id": application_id,
                "watermark": snapshot.watermark,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_at": snapshot.generated_at,
            },
        )


async def reconcile_snapshot(
    sessions: async_sessionmaker[AsyncSession], snapshot: ExampleRecordSnapshot
) -> dict[str, Any]:
    validate_snapshot(snapshot)
    expected = {
        str(record.record_id): (
            record.name,
            record.state,
            record.owner_subject,
            record.aggregate_version,
        )
        for record in snapshot.records
    }
    async with sessions() as session:
        rows = (
            await session.execute(
                sa.text(
                    """
                    SELECT record_id, name, state, owner_subject, aggregate_version
                    FROM platform_projection.example_record_projection
                    WHERE producer_application_id = :application_id
                      AND deleted_at IS NULL
                    """
                ),
                {"application_id": snapshot.producer_application_id},
            )
        ).mappings().all()
        checkpoint = (
            await session.execute(
                sa.text(
                    """
                    SELECT last_source_sequence, last_snapshot_watermark
                    FROM platform_projection.projection_checkpoint
                    WHERE producer_application_id = :application_id
                    """
                ),
                {"application_id": snapshot.producer_application_id},
            )
        ).mappings().one_or_none()
    actual = {
        str(row["record_id"]): (
            row["name"],
            row["state"],
            row["owner_subject"],
            row["aggregate_version"],
        )
        for row in rows
    }
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    mismatched = sorted(
        key for key in set(expected) & set(actual) if expected[key] != actual[key]
    )
    checkpoint_watermark = int(checkpoint["last_source_sequence"]) if checkpoint else -1
    consistent = (
        not missing
        and not unexpected
        and not mismatched
        and checkpoint_watermark >= snapshot.watermark
    )
    return {
        "consistent": consistent,
        "snapshot_id": str(snapshot.snapshot_id),
        "snapshot_watermark": snapshot.watermark,
        "checkpoint_watermark": checkpoint_watermark,
        "missing_record_ids": missing,
        "unexpected_record_ids": unexpected,
        "mismatched_record_ids": mismatched,
        "checked_at": datetime.now(UTC).isoformat(),
    }
