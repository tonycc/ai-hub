from __future__ import annotations

import asyncio
import json
import logging
import signal
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import aio_pika
from ai_hub_sdk import CloudEvent
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_hub_platform.config import ProjectionWorkerSettings
from ai_hub_platform.modules.projection.service import (
    ProjectionContractError,
    ProjectionService,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkerMetrics:
    received: int = 0
    applied: int = 0
    duplicate: int = 0
    stale: int = 0
    gap: int = 0
    snapshot_covered: int = 0
    dead_lettered: int = 0
    retried: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "received": self.received,
            "applied": self.applied,
            "duplicate": self.duplicate,
            "stale": self.stale,
            "gap": self.gap,
            "snapshot_covered": self.snapshot_covered,
            "dead_lettered": self.dead_lettered,
            "retried": self.retried,
        }


class ProjectionWorker:
    def __init__(self, settings: ProjectionWorkerSettings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{uuid4()}"
        self.engine: AsyncEngine = create_async_engine(
            settings.projection_database_url, pool_pre_ping=True
        )
        self.sessions = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.service = ProjectionService()
        self.metrics = WorkerMetrics()

    async def close(self) -> None:
        await self.engine.dispose()

    async def handle(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        self.metrics.received += 1
        try:
            event = CloudEvent.model_validate_json(message.body)
            if self.settings.processing_delay_seconds:
                await asyncio.sleep(self.settings.processing_delay_seconds)
            result = await self.service.process(self.sessions, event)
        except (ValidationError, ProjectionContractError, UnicodeDecodeError) as error:
            self.metrics.dead_lettered += 1
            LOGGER.error(
                json.dumps(
                    {
                        "event": "projection_event_rejected",
                        "message_id": message.message_id,
                        "error_type": type(error).__name__,
                        "metrics": self.metrics.as_dict(),
                    },
                    separators=(",", ":"),
                )
            )
            await message.reject(requeue=False)
            return
        except Exception:
            delivery_count = _delivery_count(message.headers)
            if delivery_count + 1 >= self.settings.max_redeliveries:
                self.metrics.dead_lettered += 1
                LOGGER.exception(
                    json.dumps(
                        {
                            "event": "projection_event_retries_exhausted",
                            "message_id": message.message_id,
                            "delivery_count": delivery_count,
                            "metrics": self.metrics.as_dict(),
                        },
                        separators=(",", ":"),
                    )
                )
                await message.reject(requeue=False)
            else:
                self.metrics.retried += 1
                LOGGER.exception(
                    json.dumps(
                        {
                            "event": "projection_event_retrying",
                            "message_id": message.message_id,
                            "delivery_count": delivery_count,
                            "metrics": self.metrics.as_dict(),
                        },
                        separators=(",", ":"),
                    )
                )
                await message.nack(requeue=True)
            return
        metric_name = result.result.lower()
        setattr(self.metrics, metric_name, getattr(self.metrics, metric_name) + 1)
        if self.settings.acknowledgement_delay_seconds:
            await asyncio.sleep(self.settings.acknowledgement_delay_seconds)
        await message.ack()
        LOGGER.info(
            json.dumps(
                {
                    "event": "projection_event_processed",
                    "event_id": str(result.event_id),
                    "result": result.result,
                    "aggregate_version": result.aggregate_version,
                    "metrics": self.metrics.as_dict(),
                },
                separators=(",", ":"),
            ),
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
                LOGGER.exception(
                    json.dumps(
                        {
                            "event": "projection_connection_restarting",
                            "metrics": self.metrics.as_dict(),
                        },
                        separators=(",", ":"),
                    )
                )
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
    raw_value: Any = headers.get("x-delivery-count", 0)
    return int(raw_value) if isinstance(raw_value, int | str) else 0


async def run_projection_worker(settings: ProjectionWorkerSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)
    worker = ProjectionWorker(settings)
    try:
        await worker.run(stop)
    finally:
        await worker.close()
