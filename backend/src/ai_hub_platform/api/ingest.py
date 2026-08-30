"""Portal API for ingest configuration and ops actions (design §2.5.1).

Reads/writes authoritative configuration in platform_core and runs sync,
reconcile, rebuild, and prune against platform_raw. Reads require
platform.ingest.read; writes/actions require platform.ingest.write with CSRF.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_hub_platform.api.dependencies import (
    SessionDependency,
    get_database,
    portal_permission_dependency,
)
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.ingest.config_store import (
    IngestConfigError,
    IngestConfigStore,
    IngestEnforceNotCertifiedError,
    IngestPolicy,
    IngestPushNotIsolatedError,
    IngestSourceRow,
    IngestTransportBusyError,
    IngestTransportImmutableError,
)
from ai_hub_platform.modules.ingest.rebuild import (
    SourceRebuildNotSupported,
    rebuild,
    sync_configured_source,
)
from ai_hub_platform.modules.ingest.reconcile import (
    RebuildMode,
    prune_change_records,
    reconcile_source,
)
from ai_hub_platform.modules.ingest.source_lock import lock_ingest_source
from ai_hub_platform.modules.ingest.sources import (
    IngestSourceConfig,
    load_push_progress,
    load_sync_cursors,
    pull_export_sources,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1/ingest", tags=["platform-ingest"])

INGEST_READ = "platform.ingest.read"
INGEST_WRITE = "platform.ingest.write"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestSourceResponse(ApiModel):
    source_application_id: str
    object_type: str
    transport_mode: str = "PULL_EXPORT"
    export_base_url: str | None = None
    interval_seconds: int
    lookback_versions: int
    page_limit: int
    enabled: bool
    push_protocol_version: str | None = None
    contract_validation_mode: str = "AUDIT_ONLY"
    allow_empty_full: bool = False
    updated_at: datetime
    last_cursor: int | None = None
    last_sync_at: datetime | None = None
    last_success_at: datetime | None = None
    last_status: str | None = None


class IngestSourceUpsertRequest(ApiModel):
    source_application_id: str
    object_type: str
    transport_mode: Literal["PULL_EXPORT", "PUSH_AGENT"] = "PULL_EXPORT"
    export_base_url: str | None = None
    interval_seconds: int = 60
    lookback_versions: int = 100
    page_limit: int = 200
    enabled: bool = False
    push_protocol_version: str | None = None
    contract_validation_mode: Literal["AUDIT_ONLY", "ENFORCE"] = "AUDIT_ONLY"
    allow_empty_full: bool = False


class IngestPolicyResponse(ApiModel):
    retention_keep_versions: int
    retention_keep_days: int | None
    payload_max_bytes: int
    page_limit_default: int
    page_limit_max: int
    scheduled_reconcile_enabled: bool
    reconcile_interval_hours: int
    push_staging_retention_hours: int
    updated_at: datetime


class IngestPolicyUpdateRequest(ApiModel):
    retention_keep_versions: int = Field(ge=1, le=100000)
    retention_keep_days: int | None = Field(default=None, ge=1, le=3650)
    payload_max_bytes: int = Field(ge=1024, le=10485760)
    page_limit_default: int = Field(ge=1, le=50000)
    page_limit_max: int = Field(ge=1, le=50000)
    scheduled_reconcile_enabled: bool
    reconcile_interval_hours: int = Field(ge=1, le=168)
    push_staging_retention_hours: int | None = Field(default=None, ge=1, le=168)


class IngestConfigResponse(ApiModel):
    policy: IngestPolicyResponse
    sources: list[IngestSourceResponse]


class SyncActionRequest(ApiModel):
    mode: Literal["incremental", "full"] = "incremental"


class RebuildActionRequest(ApiModel):
    mode: RebuildMode
    source_application_id: str
    object_type: str
    confirm: bool = False


class PruneActionRequest(ApiModel):
    dry_run: bool = True


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


async def _audit(
    request: Request,
    principal: PortalPrincipal,
    *,
    action: str,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await AuditService().append_committed(
        get_database(request),
        AuditRecord(
            request_id=_request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action=action,
            result="SUCCESS",
            actor_type="user",
            actor_id=str(principal.user_id),
            application_id=None,
            target_type="ingest_config",
            target_id=target_id,
            metadata=detail or {},
        ),
    )


def _source_response(
    row: IngestSourceRow,
    cursor: dict[str, Any] | None = None,
) -> IngestSourceResponse:
    config = row.config
    return IngestSourceResponse(
        source_application_id=config.source_application_id,
        object_type=config.object_type,
        transport_mode=config.transport_mode,
        export_base_url=config.export_base_url,
        interval_seconds=config.interval_seconds,
        lookback_versions=config.lookback_versions,
        page_limit=config.page_limit,
        enabled=config.enabled,
        push_protocol_version=config.push_protocol_version,
        contract_validation_mode=config.contract_validation_mode,
        allow_empty_full=config.allow_empty_full,
        updated_at=row.updated_at,
        last_cursor=(
            int(cursor["last_cursor"])
            if cursor is not None and cursor.get("last_cursor") is not None
            else None
        ),
        last_sync_at=cursor.get("last_sync_at") if cursor else None,
        last_success_at=cursor.get("last_success_at") if cursor else None,
        last_status=(
            str(cursor["last_status"])
            if cursor is not None and cursor.get("last_status") is not None
            else None
        ),
    )


def _progress_for_source(
    row: IngestSourceRow,
    cursors: dict[tuple[str, str], dict[str, Any]],
    push_progress: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    key = (row.config.source_application_id, row.config.object_type)
    if row.config.transport_mode == "PUSH_AGENT":
        return push_progress.get(key)
    return cursors.get(key)


def _policy_response(policy: IngestPolicy) -> IngestPolicyResponse:
    return IngestPolicyResponse(
        retention_keep_versions=policy.retention_keep_versions,
        retention_keep_days=policy.retention_keep_days,
        payload_max_bytes=policy.payload_max_bytes,
        page_limit_default=policy.page_limit_default,
        page_limit_max=policy.page_limit_max,
        scheduled_reconcile_enabled=policy.scheduled_reconcile_enabled,
        reconcile_interval_hours=policy.reconcile_interval_hours,
        push_staging_retention_hours=policy.push_staging_retention_hours,
        updated_at=policy.updated_at,
    )


def _map_config_error(error: IngestConfigError) -> ApiError:
    return ApiError(400, "invalid_ingest_config", str(error))


def _map_source_config_error(error: ValueError) -> ApiError:
    return ApiError(400, "invalid_ingest_source", str(error))


def _raw_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.raw_sessions


async def _reject_if_active_push_generation(
    session: AsyncSession,
    *,
    source_application_id: str,
    object_type: str,
) -> None:
    result = await session.execute(
        text(
            """
            SELECT generation_id
            FROM platform_raw.raw_push_generation
            WHERE source_application_id = :source_application_id
              AND object_type = :object_type
              AND status IN ('OPEN', 'RECEIVING', 'COMPLETING')
            LIMIT 1
            """
        ),
        {
            "source_application_id": source_application_id,
            "object_type": object_type,
        },
    )
    if result.one_or_none() is not None:
        raise IngestTransportBusyError(
            "transport_mode cannot change while a push generation is in progress"
        )


@router.get("/config", response_model=IngestConfigResponse)
async def get_ingest_config(
    request: Request,
    session: SessionDependency,
    _principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency(INGEST_READ, application_parameter=None)),
    ],
) -> IngestConfigResponse:
    store = IngestConfigStore()
    policy = await store.get_policy(session)
    sources = await store.list_sources(session)
    try:
        async with _raw_sessions(request)() as raw_session:
            cursors = await load_sync_cursors(raw_session)
            push_progress = await load_push_progress(raw_session)
    except Exception:  # noqa: BLE001 - cursor status is best-effort for the UI
        cursors = {}
        push_progress = {}
    return IngestConfigResponse(
        policy=_policy_response(policy),
        sources=[
            _source_response(row, _progress_for_source(row, cursors, push_progress))
            for row in sources
        ],
    )


@router.put("/policy", response_model=IngestPolicyResponse)
async def put_ingest_policy(
    request: Request,
    body: IngestPolicyUpdateRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> IngestPolicyResponse:
    store = IngestConfigStore()
    existing = await store.get_policy(session)
    policy = IngestPolicy(
        retention_keep_versions=body.retention_keep_versions,
        retention_keep_days=body.retention_keep_days,
        payload_max_bytes=body.payload_max_bytes,
        page_limit_default=body.page_limit_default,
        page_limit_max=body.page_limit_max,
        scheduled_reconcile_enabled=body.scheduled_reconcile_enabled,
        reconcile_interval_hours=body.reconcile_interval_hours,
        push_staging_retention_hours=(
            existing.push_staging_retention_hours
            if body.push_staging_retention_hours is None
            else body.push_staging_retention_hours
        ),
        updated_at=datetime.now(tz=UTC),
    )
    try:
        saved = await store.save_policy(session, policy)
        await session.commit()
    except IngestConfigError as error:
        await session.rollback()
        raise _map_config_error(error) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.policy.update",
        target_id="policy",
        detail=body.model_dump(),
    )
    return _policy_response(saved)


@router.put("/sources", response_model=IngestSourceResponse)
async def put_ingest_source(
    request: Request,
    body: IngestSourceUpsertRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> IngestSourceResponse:
    try:
        config = IngestSourceConfig(
            source_application_id=body.source_application_id,
            object_type=body.object_type,
            transport_mode=body.transport_mode,
            export_base_url=body.export_base_url,
            interval_seconds=body.interval_seconds,
            lookback_versions=body.lookback_versions,
            page_limit=body.page_limit,
            enabled=body.enabled,
            push_protocol_version=body.push_protocol_version,
            contract_validation_mode=body.contract_validation_mode,
            allow_empty_full=body.allow_empty_full,
        )
    except ValueError as error:
        raise _map_source_config_error(error) from error
    store = IngestConfigStore()
    try:
        await lock_ingest_source(
            session,
            config.source_application_id,
            config.object_type,
        )
        existing = await store.get_source(
            session,
            source_application_id=config.source_application_id,
            object_type=config.object_type,
        )
        if (
            existing is not None
            and existing.config.transport_mode != config.transport_mode
        ):
            try:
                await _reject_if_active_push_generation(
                    session,
                    source_application_id=config.source_application_id,
                    object_type=config.object_type,
                )
            except IngestTransportBusyError:
                raise
            except Exception as error:  # noqa: BLE001 - fail closed if raw is unreachable
                raise IngestTransportBusyError(
                    "cannot verify in-flight push generations before changing "
                    "transport_mode"
                ) from error
        saved = await store.upsert_source(session, config)
    except IngestEnforceNotCertifiedError as error:
        raise ApiError(409, error.error_code, str(error)) from error
    except IngestPushNotIsolatedError as error:
        raise ApiError(409, error.error_code, str(error)) from error
    except IngestTransportImmutableError as error:
        raise ApiError(409, error.error_code, str(error)) from error
    except IngestTransportBusyError as error:
        raise ApiError(409, error.error_code, str(error)) from error
    await session.commit()
    await _audit(
        request,
        principal,
        action="platform.ingest.source.upsert",
        target_id=f"{config.source_application_id}:{config.object_type}",
        detail={
            "enabled": {
                "old": None if existing is None else existing.config.enabled,
                "new": config.enabled,
            },
            "transport_mode": {
                "old": None if existing is None else existing.config.transport_mode,
                "new": config.transport_mode,
            },
            "contract_validation_mode": {
                "old": None
                if existing is None
                else existing.config.contract_validation_mode,
                "new": config.contract_validation_mode,
            },
        },
    )
    return _source_response(saved)


@router.post("/actions/sync")
async def action_sync(
    request: Request,
    body: SyncActionRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
    ) -> dict[str, Any]:
    settings = request.app.state.settings
    store = IngestConfigStore()
    enabled = pull_export_sources(await store.list_enabled_source_configs(session))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for config in enabled:
        try:
            result = await sync_configured_source(
                settings,
                source_application_id=config.source_application_id,
                object_type=config.object_type,
                force_full=body.mode == "full",
            )
            results.append(result)
        except Exception as error:  # noqa: BLE001 - report per-source failure
            errors.append(
                {
                    "source_application_id": config.source_application_id,
                    "object_type": config.object_type,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    await _audit(
        request,
        principal,
        action="platform.ingest.action.sync",
        detail={"mode": body.mode, "succeeded": len(results), "failed": len(errors)},
    )
    if errors:
        raise ApiError(
            502,
            "ingest_sync_failed",
            f"{len(errors)} of {len(results) + len(errors)} source syncs failed",
            {"errors": errors, "results": results},
        )
    return {"mode": body.mode, "succeeded": len(results), "failed": errors, "results": results}


@router.post("/actions/reconcile")
async def action_reconcile(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> dict[str, Any]:
    store = IngestConfigStore()
    enabled = await store.list_enabled_source_configs(session)
    sessions = _raw_sessions(request)
    reports: list[dict[str, Any]] = []
    for config in enabled:
        report = await reconcile_source(
            sessions,
            source_application_id=config.source_application_id,
            object_type=config.object_type,
        )
        reports.append(report.as_dict())
    drifted = [report for report in reports if report["drifted"]]
    await _audit(
        request,
        principal,
        action="platform.ingest.action.reconcile",
        detail={"sources": len(reports), "drifted": len(drifted)},
    )
    return {"sources": len(reports), "drifted": len(drifted), "reports": reports}


@router.post("/actions/rebuild")
async def action_rebuild(
    request: Request,
    body: RebuildActionRequest,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not body.confirm:
        raise ApiError(
            400,
            "ingest_rebuild_unconfirmed",
            "Rebuild is destructive; resubmit with confirm=true after operator review",
        )
    try:
        result = await rebuild(
            settings,
            mode=body.mode,
            source_application_id=body.source_application_id,
            object_type=body.object_type,
        )
    except SourceRebuildNotSupported as error:
        raise ApiError(409, error.error_code, str(error)) from error
    except ValueError as error:
        raise ApiError(400, "ingest_rebuild_rejected", str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise ApiError(502, "ingest_rebuild_failed", f"{type(error).__name__}: {error}") from error
    await _audit(
        request,
        principal,
        action="platform.ingest.action.rebuild",
        target_id=f"{body.source_application_id}:{body.object_type}",
        detail={"mode": body.mode},
    )
    return result


@router.post("/actions/prune")
async def action_prune(
    request: Request,
    body: PruneActionRequest,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                INGEST_WRITE, application_parameter=None, require_csrf=True
            )
        ),
    ],
) -> dict[str, Any]:
    store = IngestConfigStore()
    policy = await store.get_policy(session)
    sessions = _raw_sessions(request)
    try:
        result = await prune_change_records(
            sessions,
            keep_versions=policy.retention_keep_versions,
            keep_days=policy.retention_keep_days,
            dry_run=body.dry_run,
        )
    except ValueError as error:
        raise ApiError(400, "ingest_prune_rejected", str(error)) from error
    await _audit(
        request,
        principal,
        action="platform.ingest.action.prune",
        detail={
            "dry_run": body.dry_run,
            "candidates": result["candidates"],
            "deleted": result["deleted"],
        },
    )
    return result
