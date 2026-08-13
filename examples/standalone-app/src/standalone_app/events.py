from __future__ import annotations

import asyncio
import json
import logging
import signal
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import aio_pika
import sqlalchemy as sa
from ai_hub_sdk import (
    CloudEvent,
    EventActor,
    ExampleRecordSnapshot,
    ExampleRecordSnapshotItem,
    example_record_snapshot_checksum,
)
from pamqp.commands import Basic
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from standalone_app.config import EventPublisherSettings

LOGGER = logging.getLogger(__name__)

EVENT_TYPE_CHANGED = "company.example.record.changed.v1"
EVENT_TYPE_DELETED = "company.example.record.deleted.v1"
EVENT_DATA_SCHEMA = (
    "https://ai-hub.example.internal/contracts/events/"
    "example-record-event-data.v1.schema.json"
)
EVENT_SOURCE = "urn:ai-hub:application:standalone-example"
EVENT_OBJECT_TYPE = "example_record"


@dataclass(slots=True)
class PublisherMetrics:
    leases_recovered: int = 0
    claimed: int = 0
    confirmed: int = 0
    retried: int = 0
    exhausted: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "leases_recovered": self.leases_recovered,
            "claimed": self.claimed,
            "confirmed": self.confirmed,
            "retried": self.retried,
            "exhausted": self.exhausted,
        }


async def append_record_event(
    session: AsyncSession,
    *,
    application_id: str,
    event_type: str,
    record_id: UUID,
    name: str,
    state: str,
    owner_subject: str,
    aggregate_version: int,
    actor_type: Literal["user", "service", "system"],
    actor_id: str,
    trace_id: str | None,
    occurred_at: datetime | None = None,
) -> CloudEvent:
    """Append an event inside the caller's business transaction."""

    if event_type not in {EVENT_TYPE_CHANGED, EVENT_TYPE_DELETED}:
        raise ValueError("Unsupported example record event type")
    sequence_row = (
        await session.execute(
            sa.text(
                """
                UPDATE app.integration_source_state
                SET current_sequence = current_sequence + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE application_id = :application_id
                RETURNING current_sequence
                """
            ),
            {"application_id": application_id},
        )
    ).mappings().one_or_none()
    if sequence_row is None:
        raise RuntimeError("EVENT_PUBLISHER capability is not installed for the application")
    source_sequence = int(sequence_row["current_sequence"])
    data: dict[str, Any] = {"record_id": str(record_id)}
    if event_type == EVENT_TYPE_CHANGED:
        data.update(
            name=name,
            state=state,
            owner_subject=owner_subject,
        )
    event = CloudEvent(
        id=uuid4(),
        source=EVENT_SOURCE,
        type=event_type,
        subject=f"example-record/{record_id}",
        time=occurred_at or datetime.now(UTC),
        dataschema=EVENT_DATA_SCHEMA,
        producer_application_id=application_id,
        event_version=1,
        aggregate_version=aggregate_version,
        source_sequence=source_sequence,
        object_type=EVENT_OBJECT_TYPE,
        trace_id=trace_id,
        actor=EventActor(type=actor_type, id=actor_id),
        data_classification="internal",
        data=data,
    )
    await session.execute(
        sa.text(
            """
            INSERT INTO app.integration_outbox
                (event_id, event_type, source, subject, occurred_at, payload,
                 headers, status, attempts, next_attempt_at, source_sequence)
            VALUES
                (:event_id, :event_type, :source, :subject, :occurred_at,
                 CAST(:payload AS jsonb), CAST(:headers AS jsonb), 'PENDING', 0,
                 CURRENT_TIMESTAMP, :source_sequence)
            """
        ),
        {
            "event_id": event.id,
            "event_type": event.type,
            "source": event.source,
            "subject": event.subject,
            "occurred_at": event.time,
            "payload": event.model_dump_json(exclude_none=False),
            "headers": json.dumps(
                {
                    "content_type": "application/cloudevents+json",
                    "schema_version": 1,
                }
            ),
            "source_sequence": event.source_sequence,
        },
    )
    return event


async def export_snapshot(
    session_factory: async_sessionmaker[AsyncSession], *, application_id: str
) -> ExampleRecordSnapshot:
    """Export records and the Outbox watermark from one repeatable-read transaction."""

    async with session_factory() as session:
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        watermark = (
            await session.execute(
                sa.text(
                    """
                    SELECT current_sequence FROM app.integration_source_state
                    WHERE application_id = :application_id
                    """
                ),
                {"application_id": application_id},
            )
        ).scalar_one()
        rows = (
            await session.execute(
                sa.text(
                    """
                    SELECT id, name, state, owner_subject, aggregate_version, updated_at
                    FROM app.example_record
                    WHERE state <> 'DELETED'
                    ORDER BY id
                    """
                )
            )
        ).mappings().all()
        records = [
            ExampleRecordSnapshotItem(
                record_id=row["id"],
                name=row["name"],
                state=row["state"],
                owner_subject=row["owner_subject"],
                aggregate_version=row["aggregate_version"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        snapshot = ExampleRecordSnapshot(
            producer_application_id=application_id,
            watermark=int(watermark),
            records=records,
            checksum=example_record_snapshot_checksum(
                records,
                producer_application_id=application_id,
                watermark=int(watermark),
            ),
        )
        await session.rollback()
        return snapshot


class OutboxPublisher:
    def __init__(self, settings: EventPublisherSettings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{uuid4()}"
        self.engine: AsyncEngine = create_async_engine(
            settings.publisher_database_url, pool_pre_ping=True
        )
        self.sessions = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.metrics = PublisherMetrics()

    async def close(self) -> None:
        await self.engine.dispose()

    async def recover_expired_leases(self) -> int:
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    sa.text(
                        """
                        UPDATE app.integration_outbox
                        SET status = 'PENDING', locked_by = NULL, lock_expires_at = NULL,
                            next_attempt_at = CURRENT_TIMESTAMP,
                            last_error = 'publisher lease expired before confirmation'
                        WHERE status = 'PUBLISHING'
                          AND lock_expires_at < CURRENT_TIMESTAMP
                        """
                    )
                ),
            )
            recovered = int(result.rowcount)
            self.metrics.leases_recovered += recovered
            if recovered:
                LOGGER.warning(
                    json.dumps(
                        {
                            "event": "outbox_leases_recovered",
                            "recovered": recovered,
                            "metrics": self.metrics.as_dict(),
                        },
                        separators=(",", ":"),
                    )
                )
            return recovered

    async def claim_batch(self) -> list[dict[str, Any]]:
        async with self.sessions.begin() as session:
            rows = (
                await session.execute(
                    sa.text(
                        """
                        WITH candidates AS (
                            SELECT event_id
                            FROM app.integration_outbox
                            WHERE status = 'PENDING'
                              AND COALESCE(next_attempt_at, CURRENT_TIMESTAMP)
                                  <= CURRENT_TIMESTAMP
                            ORDER BY source_sequence
                            FOR UPDATE SKIP LOCKED
                            LIMIT :batch_size
                        )
                        UPDATE app.integration_outbox AS target
                        SET status = 'PUBLISHING',
                            attempts = attempts + 1,
                            locked_by = :worker_id,
                            lock_expires_at = CURRENT_TIMESTAMP
                                + make_interval(secs => :lease_seconds)
                        FROM candidates
                        WHERE target.event_id = candidates.event_id
                        RETURNING target.event_id, target.event_type, target.payload,
                                  target.headers, target.attempts, target.source_sequence
                        """
                    ),
                    {
                        "batch_size": self.settings.batch_size,
                        "worker_id": self.worker_id,
                        "lease_seconds": self.settings.lease_seconds,
                    },
                )
            ).mappings().all()
            self.metrics.claimed += len(rows)
            return [dict(row) for row in rows]

    async def mark_published(self, event_id: UUID) -> None:
        async with self.sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    sa.text(
                        """
                        UPDATE app.integration_outbox
                        SET status = 'PUBLISHED', published_at = CURRENT_TIMESTAMP,
                            locked_by = NULL, lock_expires_at = NULL, last_error = NULL
                        WHERE event_id = :event_id AND status = 'PUBLISHING'
                          AND locked_by = :worker_id
                        """
                    ),
                    {"event_id": event_id, "worker_id": self.worker_id},
                ),
            )
            if result.rowcount != 1:
                raise RuntimeError("Outbox publisher lost its event lease")

    async def mark_failed(self, event_id: UUID, attempts: int, error: Exception) -> None:
        exhausted = attempts >= self.settings.max_attempts
        if exhausted:
            self.metrics.exhausted += 1
        else:
            self.metrics.retried += 1
        delay_seconds = min(
            self.settings.retry_max_seconds,
            self.settings.retry_base_seconds * (2 ** max(0, attempts - 1)),
        )
        async with self.sessions.begin() as session:
            await session.execute(
                sa.text(
                    """
                    UPDATE app.integration_outbox
                    SET status = :status, locked_by = NULL, lock_expires_at = NULL,
                        next_attempt_at = CASE WHEN :exhausted THEN NULL ELSE
                            CURRENT_TIMESTAMP + make_interval(secs => :delay_seconds) END,
                        last_error = :last_error
                    WHERE event_id = :event_id AND locked_by = :worker_id
                    """
                ),
                {
                    "status": "FAILED" if exhausted else "PENDING",
                    "exhausted": exhausted,
                    "delay_seconds": delay_seconds,
                    "last_error": str(error)[:2000],
                    "event_id": event_id,
                    "worker_id": self.worker_id,
                },
            )

    async def publish_batch(self, exchange: aio_pika.abc.AbstractExchange) -> int:
        rows = await self.claim_batch()
        published = 0
        for row in rows:
            event_id = cast(UUID, row["event_id"])
            try:
                payload = row["payload"]
                if not isinstance(payload, dict):
                    raise ValueError("Outbox payload is not a JSON object")
                event = CloudEvent.model_validate(payload)
                message = aio_pika.Message(
                    body=event.model_dump_json(exclude_none=False).encode(),
                    content_type="application/cloudevents+json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=str(event.id),
                    type=event.type,
                    timestamp=event.time,
                    headers={
                        "producer_application_id": event.producer_application_id,
                        "source_sequence": event.source_sequence,
                        "aggregate_version": event.aggregate_version,
                    },
                )
                confirmation = await exchange.publish(
                    message,
                    routing_key=event.type,
                    mandatory=True,
                    timeout=self.settings.publish_timeout_seconds,
                )
                if not isinstance(confirmation, Basic.Ack):
                    raise RuntimeError("RabbitMQ did not positively confirm the event")
                await self.mark_published(event_id)
                published += 1
                self.metrics.confirmed += 1
                LOGGER.info(
                    json.dumps(
                        {
                            "event": "outbox_event_confirmed",
                            "event_id": str(event_id),
                            "source_sequence": event.source_sequence,
                            "attempts": row["attempts"],
                            "metrics": self.metrics.as_dict(),
                        },
                        separators=(",", ":"),
                    )
                )
            except Exception as error:
                await self.mark_failed(event_id, int(row["attempts"]), error)
                LOGGER.warning(
                    json.dumps(
                        {
                            "event": "outbox_event_publish_failed",
                            "event_id": str(event_id),
                            "attempts": int(row["attempts"]),
                            "exhausted": int(row["attempts"])
                            >= self.settings.max_attempts,
                            "error_type": type(error).__name__,
                            "metrics": self.metrics.as_dict(),
                        },
                        separators=(",", ":"),
                    )
                )
        return published

    async def run(self, stop: asyncio.Event) -> None:
        await self.recover_expired_leases()
        connection = await aio_pika.connect_robust(
            self.settings.rabbitmq_url,
            timeout=self.settings.connection_timeout_seconds,
            client_properties={"connection_name": self.worker_id},
        )
        try:
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            exchange = await channel.get_exchange(self.settings.exchange_name, ensure=False)
            while not stop.is_set():
                await self.recover_expired_leases()
                processed = await self.publish_batch(exchange)
                if processed == 0:
                    try:
                        await asyncio.wait_for(
                            stop.wait(), timeout=self.settings.poll_interval_seconds
                        )
                    except TimeoutError:
                        pass
        finally:
            await connection.close()


async def run_outbox_publisher(settings: EventPublisherSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)
    publisher = OutboxPublisher(settings)
    try:
        await publisher.run(stop)
    finally:
        await publisher.close()
