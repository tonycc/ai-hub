"""Inbound PUSH_AGENT transport API (ADR-033 / C1-A)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_hub_platform.api.dependencies import (
    Principal,
    SessionDependency,
    get_database,
    principal_dependency,
)
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.ingest.config_store import IngestConfigStore, IngestPolicy
from ai_hub_platform.modules.ingest.contract import load_active_contract
from ai_hub_platform.modules.ingest.generation import (
    PUSH_MAX_BATCHES,
    PUSH_MAX_GENERATION_BYTES,
    PUSH_MAX_GENERATION_LIFETIME,
    PUSH_MAX_GENERATION_ROWS,
    PUSH_PROTOCOL_VERSION,
    PushGenerationService,
    PushIngestError,
)
from ai_hub_platform.modules.ingest.generation_sql import SqlGenerationStore
from ai_hub_platform.modules.ingest.service import IngestRecord
from ai_hub_platform.modules.ingest.source_lock import lock_ingest_source
from ai_hub_platform.modules.ingest.sources import (
    CHANGE_RECORD_PURPOSE_UNIQUE,
    POSTGRES_BIGINT_MAX,
    PUSH_BATCH_RECORDS_ABSOLUTE_MAX,
    PUSH_CONTRACT_VERSION_MAX_LENGTH,
    PUSH_EXTERNAL_ID_MAX_LENGTH,
    PUSH_OBJECT_ID_MAX_LENGTH,
    PUSH_OBJECT_TYPE_MAX_LENGTH,
    SOURCE_APPLICATION_ID_MAX_LENGTH,
    IngestSourceConfig,
)

router = APIRouter(prefix="/platform-api/v1/ingest/push", tags=["ingest-push"])
PUSH_SCOPE = "ai_hub.ingest.push"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilitiesResponse(ApiModel):
    enabled: bool
    protocol_versions: list[str]
    contract_required: bool
    payload_max_bytes: int
    page_limit_max: int
    active_generation_limit: int = 1
    max_batches: int = PUSH_MAX_BATCHES
    max_generation_rows: int = PUSH_MAX_GENERATION_ROWS
    max_generation_bytes: int = PUSH_MAX_GENERATION_BYTES
    max_generation_lifetime_seconds: int = int(
        PUSH_MAX_GENERATION_LIFETIME.total_seconds()
    )


class CreateGenerationRequest(ApiModel):
    source_application_id: str = Field(
        min_length=1, max_length=SOURCE_APPLICATION_ID_MAX_LENGTH
    )
    object_type: str = Field(min_length=1, max_length=PUSH_OBJECT_TYPE_MAX_LENGTH)
    external_generation_id: str = Field(
        min_length=1, max_length=PUSH_EXTERNAL_ID_MAX_LENGTH
    )
    sync_mode: Literal["full", "incremental"]
    protocol_version: str = PUSH_PROTOCOL_VERSION
    lease_seconds: int = Field(default=60, ge=5, le=3600)
    purpose: Literal["production", "certification"] = "production"


class HeartbeatRequest(ApiModel):
    lease_seconds: int = Field(default=60, ge=5, le=3600)


class PushRecord(ApiModel):
    object_id: str = Field(min_length=1, max_length=PUSH_OBJECT_ID_MAX_LENGTH)
    operation: Literal["upsert", "delete"]
    version: int = Field(ge=1, le=POSTGRES_BIGINT_MAX)
    payload: dict[str, Any] | None = None


class SubmitBatchRequest(ApiModel):
    sequence_no: int = Field(ge=1)
    external_batch_id: str = Field(min_length=1, max_length=PUSH_EXTERNAL_ID_MAX_LENGTH)
    payload_contract_version: str = Field(
        min_length=1, max_length=PUSH_CONTRACT_VERSION_MAX_LENGTH
    )
    high_watermark: int = Field(ge=0, le=POSTGRES_BIGINT_MAX)
    content_sha256: str
    schema_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    records: list[PushRecord] = Field(max_length=PUSH_BATCH_RECORDS_ABSOLUTE_MAX)


class CompleteGenerationRequest(ApiModel):
    expected_batch_count: int = Field(ge=0)
    total_rows: int = Field(ge=0)
    ordered_batch_digest: str
    high_watermark: int = Field(ge=0, le=POSTGRES_BIGINT_MAX)
    confirm_empty_full: bool = False


def _push_principal() -> Any:
    return principal_dependency(PUSH_SCOPE, actor_types=("service",))


def _require_push_enabled(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.data_ingest_push_enabled:
        raise ApiError(
            503,
            "data_ingest_push_disabled",
            "DATA_INGEST Push is disabled; existing Pull ingest is unaffected",
        )
    if not CHANGE_RECORD_PURPOSE_UNIQUE:
        raise ApiError(
            503,
            "ingest_push_change_log_not_isolated",
            "DATA_INGEST Push cannot be enabled until certification change "
            "records are isolated from the production unique key",
        )


def _raw_sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.raw_sessions


@asynccontextmanager
async def _raw_tx(request: Request) -> AsyncGenerator[AsyncSession]:
    async with _raw_sessions(request)() as session:
        async with session.begin():
            yield session


def _map_push_error(error: PushIngestError) -> ApiError:
    return ApiError(error.status_code, error.error_code, str(error), error.details)


def _failed_completion_error(payload: dict[str, Any]) -> ApiError:
    code = payload.get("error_code")
    error_code = (
        code if isinstance(code, str) and code else "generation_complete_mismatch"
    )
    return ApiError(400, error_code, "generation failed during completion")


def _caller_application_id(principal: Principal) -> str:
    application_id = principal.token.application_id
    if not application_id:
        raise ApiError(
            403,
            "source_impersonation_denied",
            "service token is missing application_id claim",
        )
    return application_id


async def _audit_push(
    request: Request,
    principal: Principal,
    *,
    action: str,
    target_id: str,
    metadata: dict[str, Any],
) -> None:
    await AuditService().append_committed(
        get_database(request),
        AuditRecord(
            request_id=str(getattr(request.state, "request_id", "")),
            trace_id=getattr(request.state, "trace_id", None),
            action=action,
            result="SUCCESS",
            actor_type="service",
            actor_id=principal.token.subject,
            application_id=principal.token.application_id,
            target_type="push_generation",
            target_id=target_id,
            metadata=metadata,
        ),
    )


def _push_service(
    session: AsyncSession,
    policy: IngestPolicy,
    request: Request | None = None,
    principal: Principal | None = None,
) -> PushGenerationService:
    actor = None if principal is None else principal.token.subject
    request_id: str | None = None
    if request is not None:
        raw_request_id = str(getattr(request.state, "request_id", "") or "")
        request_id = raw_request_id or None
    return PushGenerationService(
        SqlGenerationStore(session),
        payload_max_bytes=policy.payload_max_bytes,
        batch_row_limit=policy.page_limit_max,
        actor=actor,
        request_id=request_id,
    )


async def _load_source(
    session: SessionDependency,
    *,
    source_application_id: str,
    object_type: str,
    caller_application_id: str,
) -> IngestSourceConfig:
    if caller_application_id != source_application_id:
        raise ApiError(
            403,
            "source_impersonation_denied",
            "token application_id does not match the registered source",
        )
    row = await IngestConfigStore().get_source(
        session,
        source_application_id=source_application_id,
        object_type=object_type,
    )
    if row is None:
        raise ApiError(404, "ingest_source_not_found", "ingest source is not registered")
    return row.config


async def _lock_then_load_source(
    raw_session: AsyncSession,
    core_session: AsyncSession,
    *,
    source_application_id: str,
    object_type: str,
    caller_application_id: str,
) -> IngestSourceConfig:
    await lock_ingest_source(raw_session, source_application_id, object_type)
    return await _load_source(
        core_session,
        source_application_id=source_application_id,
        object_type=object_type,
        caller_application_id=caller_application_id,
    )


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_push_capabilities(
    request: Request,
    session: SessionDependency,
    _principal: Annotated[Principal, Depends(_push_principal())],
) -> CapabilitiesResponse:
    settings = request.app.state.settings
    policy = await IngestConfigStore().get_policy(session)
    return CapabilitiesResponse(
        enabled=bool(settings.data_ingest_push_enabled)
        and CHANGE_RECORD_PURPOSE_UNIQUE,
        protocol_versions=[PUSH_PROTOCOL_VERSION],
        contract_required=True,
        payload_max_bytes=policy.payload_max_bytes,
        page_limit_max=policy.page_limit_max,
        max_batches=PUSH_MAX_BATCHES,
        max_generation_rows=PUSH_MAX_GENERATION_ROWS,
        max_generation_bytes=PUSH_MAX_GENERATION_BYTES,
        max_generation_lifetime_seconds=int(
            PUSH_MAX_GENERATION_LIFETIME.total_seconds()
        ),
    )


@router.post("/generations")
async def create_generation(
    request: Request,
    body: CreateGenerationRequest,
    session: SessionDependency,
    principal: Annotated[Principal, Depends(_push_principal())],
) -> dict[str, Any]:
    _require_push_enabled(request)
    caller = _caller_application_id(principal)
    policy = await IngestConfigStore().get_policy(session)
    try:
        async with _raw_tx(request) as raw_session:
            source = await _lock_then_load_source(
                raw_session,
                session,
                source_application_id=body.source_application_id,
                object_type=body.object_type,
                caller_application_id=caller,
            )
            contract = await load_active_contract(
                session,
                source_application_id=body.source_application_id,
                object_type=body.object_type,
            )
            generation = await _push_service(
                raw_session, policy, request, principal
            ).create_generation(
                source=source,
                contract=contract,
                caller_application_id=caller,
                external_generation_id=body.external_generation_id,
                sync_mode=body.sync_mode,
                request=body.model_dump(),
                lease_seconds=body.lease_seconds,
                protocol_version=body.protocol_version,
                purpose=body.purpose,
            )
            result = generation.as_dict()
        await _audit_push(
            request,
            principal,
            action="platform.ingest.push.generation.create",
            target_id=str(result["generation_id"]),
            metadata={
                "source_application_id": body.source_application_id,
                "object_type": body.object_type,
                "sync_mode": body.sync_mode,
                "external_generation_id": body.external_generation_id,
                "purpose": body.purpose,
            },
        )
        return result
    except PushIngestError as error:
        raise _map_push_error(error) from error


@router.get("/generations/{generation_id}")
async def get_generation(
    request: Request,
    generation_id: UUID,
    session: SessionDependency,
    principal: Annotated[Principal, Depends(_push_principal())],
) -> dict[str, Any]:
    _require_push_enabled(request)
    caller = _caller_application_id(principal)
    policy = await IngestConfigStore().get_policy(session)
    try:
        async with _raw_tx(request) as raw_session:
            generation = await _push_service(
                raw_session, policy, request, principal
            ).peek_generation(
                generation_id
            )
    except PushIngestError as error:
        raise _map_push_error(error) from error
    if generation.source_application_id != caller:
        raise ApiError(
            403,
            "source_impersonation_denied",
            "generation belongs to another source",
        )
    return generation.as_dict()


@router.post("/generations/{generation_id}/heartbeat")
async def heartbeat_generation(
    request: Request,
    generation_id: UUID,
    body: HeartbeatRequest,
    session: SessionDependency,
    principal: Annotated[Principal, Depends(_push_principal())],
) -> dict[str, Any]:
    _require_push_enabled(request)
    caller = _caller_application_id(principal)
    policy = await IngestConfigStore().get_policy(session)
    try:
        async with _raw_tx(request) as raw_session:
            service = _push_service(raw_session, policy, request, principal)
            generation = await service.peek_generation(generation_id)
            if generation.source_application_id != caller:
                raise ApiError(
                    403,
                    "source_impersonation_denied",
                    "generation belongs to another source",
                )
            source = await _lock_then_load_source(
                raw_session,
                session,
                source_application_id=generation.source_application_id,
                object_type=generation.object_type,
                caller_application_id=caller,
            )
            generation = await service.heartbeat(
                generation_id,
                source=source,
                caller_application_id=caller,
                lease_seconds=body.lease_seconds,
            )
            return generation.as_dict()
    except PushIngestError as error:
        raise _map_push_error(error) from error


@router.post("/generations/{generation_id}/batches")
async def submit_batch(
    request: Request,
    generation_id: UUID,
    body: SubmitBatchRequest,
    session: SessionDependency,
    principal: Annotated[Principal, Depends(_push_principal())],
) -> dict[str, Any]:
    _require_push_enabled(request)
    caller = _caller_application_id(principal)
    policy = await IngestConfigStore().get_policy(session)
    try:
        async with _raw_tx(request) as raw_session:
            service = _push_service(raw_session, policy, request, principal)
            generation = await service.peek_generation(generation_id)
            if generation.source_application_id != caller:
                raise ApiError(
                    403,
                    "source_impersonation_denied",
                    "generation belongs to another source",
                )
            if len(body.records) > policy.page_limit_max:
                raise ApiError(
                    400,
                    "batch_too_large",
                    "batch exceeds page_limit_max",
                    {"page_limit_max": policy.page_limit_max},
                )
            records = [
                IngestRecord(
                    item.object_id, item.operation, item.version, item.payload
                )
                for item in body.records
            ]
            source = await _lock_then_load_source(
                raw_session,
                session,
                source_application_id=generation.source_application_id,
                object_type=generation.object_type,
                caller_application_id=caller,
            )
            contract = await load_active_contract(
                session,
                source_application_id=generation.source_application_id,
                object_type=generation.object_type,
                contract_version=(
                    generation.payload_contract_version or body.payload_contract_version
                ),
                statuses=("ACTIVE", "DEPRECATED"),
            )
            result = await service.submit_batch(
                generation_id,
                source=source,
                contract=contract,
                caller_application_id=caller,
                sequence_no=body.sequence_no,
                external_batch_id=body.external_batch_id,
                records=records,
                high_watermark=body.high_watermark,
                payload_contract_version=body.payload_contract_version,
                content_sha256=body.content_sha256,
                schema_fingerprint=body.schema_fingerprint,
            )
        await _audit_push(
            request,
            principal,
            action="platform.ingest.push.generation.batch",
            target_id=str(generation_id),
            metadata={
                "external_batch_id": body.external_batch_id,
                "sequence_no": body.sequence_no,
                "record_count": len(records),
            },
        )
        return result
    except PushIngestError as error:
        raise _map_push_error(error) from error


@router.post("/generations/{generation_id}/complete")
async def complete_generation(
    request: Request,
    generation_id: UUID,
    body: CompleteGenerationRequest,
    session: SessionDependency,
    principal: Annotated[Principal, Depends(_push_principal())],
) -> dict[str, Any]:
    _require_push_enabled(request)
    caller = _caller_application_id(principal)
    policy = await IngestConfigStore().get_policy(session)
    try:
        async with _raw_tx(request) as raw_session:
            service = _push_service(raw_session, policy, request, principal)
            generation = await service.peek_generation(generation_id)
            loaded_source = await _lock_then_load_source(
                raw_session,
                session,
                source_application_id=generation.source_application_id,
                object_type=generation.object_type,
                caller_application_id=caller,
            )
            intent = await service.complete(
                generation_id,
                source=loaded_source,
                caller_application_id=caller,
                expected_batch_count=body.expected_batch_count,
                total_rows=body.total_rows,
                ordered_batch_digest=body.ordered_batch_digest,
                high_watermark=body.high_watermark,
                confirm_empty_full=body.confirm_empty_full,
                publish=False,
            )
            result = (
                intent.as_dict()
                if intent.status in {"COMPLETED", "FAILED"}
                else None
            )
        if result is None:
            async with _raw_tx(request) as raw_session:
                service = _push_service(raw_session, policy, request, principal)
                generation = await service.peek_generation(generation_id)
                loaded_source = await _lock_then_load_source(
                    raw_session,
                    session,
                    source_application_id=generation.source_application_id,
                    object_type=generation.object_type,
                    caller_application_id=caller,
                )
                completed = await service.complete(
                    generation_id,
                    source=loaded_source,
                    caller_application_id=caller,
                    expected_batch_count=body.expected_batch_count,
                    total_rows=body.total_rows,
                    ordered_batch_digest=body.ordered_batch_digest,
                    high_watermark=body.high_watermark,
                    confirm_empty_full=body.confirm_empty_full,
                    publish=True,
                )
                result = completed.as_dict()
        if result["status"] == "FAILED":
            raise _failed_completion_error(result)
        await _audit_push(
            request,
            principal,
            action="platform.ingest.push.generation.complete",
            target_id=str(generation_id),
            metadata={
                "status": result["status"],
                "high_watermark": body.high_watermark,
            },
        )
        return result
    except PushIngestError as error:
        raise _map_push_error(error) from error


@router.post("/generations/{generation_id}/abort")
async def abort_generation(
    request: Request,
    generation_id: UUID,
    session: SessionDependency,
    principal: Annotated[Principal, Depends(_push_principal())],
) -> dict[str, Any]:
    _require_push_enabled(request)
    caller = _caller_application_id(principal)
    policy = await IngestConfigStore().get_policy(session)
    try:
        async with _raw_tx(request) as raw_session:
            service = _push_service(raw_session, policy, request, principal)
            generation = await service.peek_generation(generation_id)
            source = await _lock_then_load_source(
                raw_session,
                session,
                source_application_id=generation.source_application_id,
                object_type=generation.object_type,
                caller_application_id=caller,
            )
            generation = await service.abort(
                generation_id,
                source=source,
                caller_application_id=caller,
            )
            result = generation.as_dict()
        await _audit_push(
            request,
            principal,
            action="platform.ingest.push.generation.abort",
            target_id=str(generation_id),
            metadata={"status": result["status"]},
        )
        return result
    except PushIngestError as error:
        raise _map_push_error(error) from error
