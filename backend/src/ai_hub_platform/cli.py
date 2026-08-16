import asyncio
import json
import logging.config
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import uvicorn
from ai_hub_sdk import json_log_config
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_hub_platform.config import (
    get_raw_worker_settings,
    get_settings,
)
from ai_hub_platform.modules.conformance.service import (
    ConformanceProfile,
    ConformanceService,
)
from ai_hub_platform.modules.ingest.rebuild import rebuild, sync_configured_source
from ai_hub_platform.modules.ingest.reconcile import RebuildMode, reconcile_source
from ai_hub_platform.modules.ingest.scheduler import run_ingest_scheduler


def run() -> None:
    uvicorn.run(
        "ai_hub_platform.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=json_log_config(),
    )


def run_ingest_scheduler_cli() -> None:
    logging.config.dictConfig(json_log_config())
    asyncio.run(run_ingest_scheduler(get_raw_worker_settings()))


def _parse_ingest_pair(usage: str) -> tuple[str, str]:
    if len(sys.argv) != 3:
        raise SystemExit(usage)
    return sys.argv[1], sys.argv[2]


async def _run_ingest_reconcile(source_application_id: str, object_type: str) -> int:
    settings = get_raw_worker_settings()
    engine = create_async_engine(settings.raw_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        report = await reconcile_source(
            sessions,
            source_application_id=source_application_id,
            object_type=object_type,
        )
        print(json.dumps(report.as_dict(), sort_keys=True))
        return 1 if report.drifted else 0
    finally:
        await engine.dispose()


def run_ingest_reconcile_cli() -> None:
    logging.config.dictConfig(json_log_config())
    source_application_id, object_type = _parse_ingest_pair(
        "usage: ai-hub-ingest-reconcile SOURCE_APPLICATION_ID OBJECT_TYPE"
    )
    raise SystemExit(asyncio.run(_run_ingest_reconcile(source_application_id, object_type)))


async def _run_ingest_rebuild(
    mode: RebuildMode, source_application_id: str, object_type: str
) -> None:
    settings = get_raw_worker_settings()
    result = await rebuild(
        settings,
        mode=mode,
        source_application_id=source_application_id,
        object_type=object_type,
    )
    print(json.dumps(result, sort_keys=True, default=str))


def run_ingest_rebuild_cli() -> None:
    logging.config.dictConfig(json_log_config())
    if len(sys.argv) != 4 or sys.argv[1] not in {"log", "source"}:
        raise SystemExit(
            "usage: ai-hub-ingest-rebuild MODE SOURCE_APPLICATION_ID OBJECT_TYPE\n"
            "MODE is 'log' (replay change log) or 'source' (force full export pull)"
        )
    mode: RebuildMode = "log" if sys.argv[1] == "log" else "source"
    asyncio.run(_run_ingest_rebuild(mode, sys.argv[2], sys.argv[3]))


async def _run_ingest_sync(
    source_application_id: str,
    object_type: str,
    *,
    force_full: bool,
) -> None:
    settings = get_raw_worker_settings()
    result = await sync_configured_source(
        settings,
        source_application_id=source_application_id,
        object_type=object_type,
        force_full=force_full,
    )
    print(json.dumps(result, sort_keys=True, default=str))


def run_ingest_sync_cli() -> None:
    logging.config.dictConfig(json_log_config())
    args = [argument for argument in sys.argv[1:] if argument != "--full"]
    force_full = "--full" in sys.argv[1:]
    if len(args) != 2:
        raise SystemExit(
            "usage: ai-hub-ingest-sync SOURCE_APPLICATION_ID OBJECT_TYPE [--full]"
        )
    asyncio.run(_run_ingest_sync(args[0], args[1], force_full=force_full))


async def _run_ingest_seed(sources_path: str | None) -> None:
    from ai_hub_platform.modules.ingest.config_store import IngestConfigStore
    from ai_hub_platform.modules.ingest.sources import load_ingest_sources

    settings = get_raw_worker_settings()
    path = sources_path or settings.ingest_sources_path
    document = load_ingest_sources(path)
    # Seeding writes platform_core, which the raw worker role cannot do; prefer the
    # migrator connection when the deployment provides it (compose sets it).
    database_url = (
        settings.seed_database_url
        if settings.seed_database_url is not None
        else settings.raw_database_url
    )
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            added = await IngestConfigStore().seed_sources(session, document.sources)
        print(json.dumps({"seeded": added, "total": len(document.sources)}, sort_keys=True))
    finally:
        await engine.dispose()


def run_ingest_seed_cli() -> None:
    """Seed platform_core.ingest_source from the operations JSON (bootstrap only)."""
    logging.config.dictConfig(json_log_config())
    sources_path = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(_run_ingest_seed(sources_path))


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
