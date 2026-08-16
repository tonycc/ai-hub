"""Rebuild aggregated current state from the change log or a source full sync."""

from __future__ import annotations

from typing import Any

from ai_hub_sdk import OidcClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_hub_platform.config import RawWorkerSettings
from ai_hub_platform.modules.ingest.export_client import EXPORT_TOKEN_SCOPES, ExportClient
from ai_hub_platform.modules.ingest.reconcile import RebuildMode, rebuild_from_log
from ai_hub_platform.modules.ingest.scheduler import IngestScheduler
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig, load_ingest_sources


def _matching_ingest_source(
    settings: RawWorkerSettings,
    *,
    source_application_id: str,
    object_type: str,
) -> IngestSourceConfig:
    document = load_ingest_sources(settings.ingest_sources_path)
    matching = [
        source
        for source in document.sources
        if source.source_application_id == source_application_id
        and source.object_type == object_type
    ]
    if not matching:
        raise ValueError(
            "No ingest source configured for "
            f"{source_application_id}/{object_type} in {settings.ingest_sources_path}"
        )
    return matching[0]


async def sync_configured_source(
    settings: RawWorkerSettings,
    *,
    source_application_id: str,
    object_type: str,
    force_full: bool = False,
) -> dict[str, Any]:
    """Run one pull cycle for a configured source (incremental by default)."""
    source = _matching_ingest_source(
        settings,
        source_application_id=source_application_id,
        object_type=object_type,
    )
    engine = create_async_engine(settings.raw_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    oidc = OidcClient(
        settings.oidc_issuer,
        settings.oidc_client_id,
        settings.oidc_client_secret.get_secret_value(),
    )

    async def token_provider() -> str:
        return await oidc.client_credentials_token(EXPORT_TOKEN_SCOPES)

    export_client = ExportClient(
        token_provider=token_provider,
        timeout_seconds=settings.http_timeout_seconds,
    )
    scheduler = IngestScheduler(
        settings,
        sources=[source],
        sessions=sessions,
        export_client=export_client,
    )
    try:
        # None lets the scheduler choose full when the cursor is still zero.
        return await scheduler.sync_source(
            source,
            force_full=True if force_full else None,
        )
    finally:
        await export_client.close()
        await oidc.close()
        await engine.dispose()


async def rebuild_from_source(
    settings: RawWorkerSettings,
    *,
    source_application_id: str,
    object_type: str,
) -> dict[str, Any]:
    """Force a full export pull for one (app, object_type) and advance the cursor."""
    result = await sync_configured_source(
        settings,
        source_application_id=source_application_id,
        object_type=object_type,
        force_full=True,
    )
    return {"mode": "source", **result}


async def rebuild(
    settings: RawWorkerSettings,
    *,
    mode: RebuildMode,
    source_application_id: str,
    object_type: str,
) -> dict[str, Any]:
    if mode == "log":
        engine = create_async_engine(settings.raw_database_url, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            return await rebuild_from_log(
                sessions,
                source_application_id=source_application_id,
                object_type=object_type,
            )
        finally:
            await engine.dispose()
    if mode == "source":
        return await rebuild_from_source(
            settings,
            source_application_id=source_application_id,
            object_type=object_type,
        )
    raise ValueError(f"unsupported rebuild mode: {mode}")
