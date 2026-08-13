from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import signal
import socket
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import aio_pika
import sqlalchemy as sa
from ai_hub_sdk import CloudEvent
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from standalone_app.config import EventConsumerSettings

LOGGER = logging.getLogger(__name__)


class ReferenceEventConsumer:
    def __init__(self, settings: EventConsumerSettings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{uuid4()}"
        self.engine: AsyncEngine = create_async_engine(
            settings.consumer_database_url,
            pool_pre_ping=True,
        )
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        await self.engine.dispose()

    async def process(self, event: CloudEvent) -> str:
        payload_hash = hashlib.sha256(
            event.model_dump_json(exclude_none=False).encode()
        ).hexdigest()
        async with self.sessions.begin() as session:
            existing_hash = await session.scalar(
                sa.text(
                    """
                    SELECT payload_hash
                    FROM app.integration_inbox
                    WHERE consumer_id = :consumer_id AND event_id = :event_id
                    """
                ),
                {"consumer_id": self.settings.consumer_id, "event_id": event.id},
            )
            if existing_hash is not None:
                if str(existing_hash) != payload_hash:
                    raise ValueError("event_id was reused with a different payload")
                return "DUPLICATE"
            await session.execute(
                sa.text(
                    """
                    INSERT INTO app.integration_inbox
                        (consumer_id, event_id, payload_hash)
                    VALUES (:consumer_id, :event_id, :payload_hash)
                    """
                ),
                {
                    "consumer_id": self.settings.consumer_id,
                    "event_id": event.id,
                    "payload_hash": payload_hash,
                },
            )
            await session.execute(
                sa.text(
                    """
                    INSERT INTO app.integration_consumer_effect
                        (event_id, event_type, source_application_id, subject)
                    VALUES
                        (:event_id, :event_type, :source_application_id, :subject)
                    """
                ),
                {
                    "event_id": event.id,
                    "event_type": event.type,
                    "source_application_id": event.producer_application_id,
                    "subject": event.subject,
                },
            )
            await session.execute(
                sa.text(
                    """
                    UPDATE app.integration_inbox
                    SET processed_at = CURRENT_TIMESTAMP,
                        result_summary = 'business-neutral effect applied'
                    WHERE consumer_id = :consumer_id AND event_id = :event_id
                    """
                ),
                {"consumer_id": self.settings.consumer_id, "event_id": event.id},
            )
        return "APPLIED"

    async def handle(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            event = CloudEvent.model_validate_json(message.body)
            result = await self.process(event)
        except (ValidationError, UnicodeDecodeError, ValueError) as error:
            LOGGER.error(
                json.dumps(
                    {
                        "event": "reference_consumer_event_rejected",
                        "message_id": message.message_id,
                        "error_type": type(error).__name__,
                    },
                    separators=(",", ":"),
                )
            )
            await message.reject(requeue=False)
            return
        except Exception:
            delivery_count = _delivery_count(message.headers)
            if delivery_count + 1 >= self.settings.max_redeliveries:
                await message.reject(requeue=False)
            else:
                await message.nack(requeue=True)
            LOGGER.exception(
                json.dumps(
                    {
                        "event": "reference_consumer_processing_failed",
                        "message_id": message.message_id,
                        "delivery_count": delivery_count,
                    },
                    separators=(",", ":"),
                )
            )
            return
        await message.ack()
        LOGGER.info(
            json.dumps(
                {
                    "event": "reference_consumer_event_processed",
                    "event_id": str(event.id),
                    "result": result,
                },
                separators=(",", ":"),
            )
        )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            connection: aio_pika.abc.AbstractConnection | None = None
            try:
                connection = await aio_pika.connect(
                    self.settings.rabbitmq_url,
                    timeout=self.settings.connection_timeout_seconds,
                    client_properties={"connection_name": self.worker_id},
                )
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=self.settings.prefetch_count)
                queue = await channel.get_queue(self.settings.queue_name, ensure=False)
                async with queue.iterator() as iterator:
                    async for message in iterator:
                        if stop.is_set():
                            break
                        await self.handle(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("reference consumer connection restarting")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass
            finally:
                if connection is not None:
                    await connection.close()


def _delivery_count(headers: Mapping[str, Any] | None) -> int:
    if headers is None:
        return 0
    value: Any = headers.get("x-delivery-count", 0)
    return int(value) if isinstance(value, int | str) else 0


async def run_reference_consumer(settings: EventConsumerSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)
    consumer = ReferenceEventConsumer(settings)
    try:
        await consumer.run(stop)
    finally:
        await consumer.close()
