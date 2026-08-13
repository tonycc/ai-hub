import asyncio
import json
import logging.config
import sys
from pathlib import Path

import uvicorn
from ai_hub_sdk import json_log_config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from standalone_app.config import (
    get_event_consumer_settings,
    get_event_publisher_settings,
    get_settings,
)
from standalone_app.consumer import run_reference_consumer
from standalone_app.events import export_snapshot, run_outbox_publisher


def run() -> None:
    uvicorn.run(
        "standalone_app.main:app",
        host="0.0.0.0",
        port=8100,
        reload=False,
        log_config=json_log_config(),
    )


def run_publisher() -> None:
    logging.config.dictConfig(json_log_config())
    asyncio.run(run_outbox_publisher(get_event_publisher_settings()))


def run_consumer() -> None:
    logging.config.dictConfig(json_log_config())
    asyncio.run(run_reference_consumer(get_event_consumer_settings()))


async def _snapshot_json() -> str:
    settings = get_settings()
    if "PROJECTION_SOURCE" not in settings.capabilities:
        raise RuntimeError("snapshot export requires PROJECTION_SOURCE capability")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        snapshot = await export_snapshot(sessions, application_id=settings.application_id)
        return snapshot.model_dump_json(indent=2)
    finally:
        await engine.dispose()


def run_snapshot_export() -> None:
    if len(sys.argv) > 2:
        raise SystemExit("usage: standalone-snapshot-export [SNAPSHOT.json]")
    snapshot_json = asyncio.run(_snapshot_json())
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(snapshot_json + "\n", encoding="utf-8")
        print(json.dumps({"snapshot_path": sys.argv[1]}))
    else:
        print(snapshot_json)
