import asyncio
import json
import logging.config
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import uvicorn
from ai_hub_sdk import ExampleRecordSnapshot, json_log_config
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_hub_platform.config import get_projection_worker_settings, get_settings
from ai_hub_platform.modules.conformance.service import (
    ConformanceProfile,
    ConformanceService,
)
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


class RuntimeEvidenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PASSED", "FAILED"]
    evidence: dict[str, Any] = Field(default_factory=dict)


class RuntimeEvidenceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    environment: str
    contract_version: str
    source: str
    verified_at: datetime
    profiles: dict[ConformanceProfile, RuntimeEvidenceProfile]


async def _import_conformance_evidence(document_json: str) -> None:
    document = RuntimeEvidenceDocument.model_validate_json(document_json)
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            rows = await ConformanceService().record_runtime_evidence(
                session,
                application_id=document.application_id,
                environment=document.environment,
                contract_version=document.contract_version,
                source=document.source,
                profiles={
                    profile: value.model_dump(mode="json")
                    for profile, value in document.profiles.items()
                },
                verified_at=document.verified_at,
            )
        print(
            json.dumps(
                {
                    "imported": True,
                    "application_id": document.application_id,
                    "environment": document.environment,
                    "profiles": sorted(row["profile"] for row in rows),
                    "evidence_sha256": sorted(row["evidence_sha256"] for row in rows),
                },
                sort_keys=True,
            )
        )
    finally:
        await engine.dispose()


def run_conformance_evidence_import_cli() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: ai-hub-conformance-evidence-import EVIDENCE.json")
    document_json = Path(sys.argv[1]).read_text(encoding="utf-8")
    asyncio.run(_import_conformance_evidence(document_json))
