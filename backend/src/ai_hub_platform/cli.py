import asyncio
import json
import logging.config
import sys
from pathlib import Path

import uvicorn
from ai_hub_sdk import ExampleRecordSnapshot, json_log_config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_hub_platform.config import get_projection_worker_settings
from ai_hub_platform.modules.projection.service import rebuild_from_snapshot, reconcile_snapshot
from ai_hub_platform.modules.projection.worker import run_projection_worker


def run() -> None:
    uvicorn.run(
        "ai_hub_platform.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=json_log_config(),
    )


def run_projection_worker_cli() -> None:
    logging.config.dictConfig(json_log_config())
    asyncio.run(run_projection_worker(get_projection_worker_settings()))


async def _with_snapshot(operation: str, snapshot_json: str) -> None:
    settings = get_projection_worker_settings()
    snapshot = ExampleRecordSnapshot.model_validate_json(snapshot_json)
    engine = create_async_engine(settings.projection_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        if operation == "rebuild":
            await rebuild_from_snapshot(sessions, snapshot)
            print(json.dumps({"rebuilt": True, "snapshot_id": str(snapshot.snapshot_id)}))
        else:
            print(json.dumps(await reconcile_snapshot(sessions, snapshot), sort_keys=True))
    finally:
        await engine.dispose()


def run_projection_rebuild_cli() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ai-hub-projection-rebuild SNAPSHOT.json")
    snapshot_json = Path(sys.argv[1]).read_text(encoding="utf-8")
    asyncio.run(_with_snapshot("rebuild", snapshot_json))


def run_projection_reconcile_cli() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ai-hub-projection-reconcile SNAPSHOT.json")
    snapshot_json = Path(sys.argv[1]).read_text(encoding="utf-8")
    asyncio.run(_with_snapshot("reconcile", snapshot_json))
