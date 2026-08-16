"""Portal governance API for aggregated data current-state and history reads."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from ai_hub_platform.api.dependencies import SessionDependency, portal_permission_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.ingest.query import (
    DataQueryService,
    DataQueryValidationError,
    merge_portal_scope,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1/data", tags=["data-governance"])

DATA_READ_PERMISSION = "platform.data.read"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CurrentStateObjectResponse(ApiModel):
    source_application_id: str
    object_type: str
    object_id: str
    version: int
    payload: dict[str, Any] | None
    payload_contract_version: str | None
    updated_at: datetime


class CurrentStateListResponse(ApiModel):
    items: list[CurrentStateObjectResponse]
    total: int
    limit: int
    offset: int


class ChangeHistoryRecordResponse(ApiModel):
    source_application_id: str
    object_type: str
    object_id: str
    operation: str
    version: int
    payload: dict[str, Any] | None
    payload_contract_version: str | None
    content_hash: str | None
    received_at: datetime
    batch_id: str


class ChangeHistoryListResponse(ApiModel):
    items: list[ChangeHistoryRecordResponse]
    total: int
    limit: int
    offset: int


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


async def _audit_success(
    request: Request,
    session: SessionDependency,
    principal: PortalPrincipal,
    *,
    action: str,
    source_application_id: str | None,
    object_type: str | None = None,
    object_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await AuditService().append(
        session,
        AuditRecord(
            request_id=_request_id(request),
            trace_id=getattr(request.state, "trace_id", None),
            action=action,
            result="SUCCESS",
            actor_type="user",
            actor_id=str(principal.user_id),
            application_id=source_application_id,
            target_type="aggregated_data",
            target_id=source_application_id,
            metadata={
                "object_type": object_type,
                "object_id": object_id,
                **(detail or {}),
            },
        ),
    )


def _map_validation(error: DataQueryValidationError) -> ApiError:
    message = str(error)
    if "outside the caller's data scope" in message:
        return ApiError(403, "platform_data_scope_denied", message)
    return ApiError(400, "invalid_data_query", message)


@router.get("/objects", response_model=CurrentStateListResponse)
async def list_aggregated_objects(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                DATA_READ_PERMISSION,
                application_parameter=None,
            )
        ),
    ],
    source_application_id: Annotated[str | None, Query()] = None,
    object_type: Annotated[str | None, Query()] = None,
    object_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CurrentStateListResponse:
    try:
        allowed = merge_portal_scope(
            principal.application_scope(DATA_READ_PERMISSION),
            source_application_id,
        )
        page = await DataQueryService().list_current_state(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            object_id=object_id,
            allowed_application_ids=allowed,
            limit=limit,
            offset=offset,
        )
    except DataQueryValidationError as error:
        raise _map_validation(error) from error

    await _audit_success(
        request,
        session,
        principal,
        action="platform.data.read",
        source_application_id=source_application_id,
        object_type=object_type,
        object_id=object_id,
        detail={"total": page.total, "limit": limit, "offset": offset},
    )
    return CurrentStateListResponse(
        items=[
            CurrentStateObjectResponse(
                source_application_id=item.source_application_id,
                object_type=item.object_type,
                object_id=item.object_id,
                version=item.version,
                payload=item.payload,
                payload_contract_version=item.payload_contract_version,
                updated_at=item.updated_at,
            )
            for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/objects/{source_application_id}/{object_type}/{object_id}",
    response_model=CurrentStateObjectResponse,
)
async def get_aggregated_object(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                DATA_READ_PERMISSION,
                application_parameter="source_application_id",
            )
        ),
    ],
    source_application_id: str,
    object_type: str,
    object_id: str,
) -> CurrentStateObjectResponse:
    try:
        allowed = merge_portal_scope(
            principal.application_scope(DATA_READ_PERMISSION),
            source_application_id,
        )
        item = await DataQueryService().get_current_state(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            object_id=object_id,
            allowed_application_ids=allowed,
        )
    except DataQueryValidationError as error:
        raise _map_validation(error) from error
    if item is None:
        raise ApiError(404, "aggregated_object_not_found", "Aggregated object not found")

    await _audit_success(
        request,
        session,
        principal,
        action="platform.data.read",
        source_application_id=source_application_id,
        object_type=object_type,
        object_id=object_id,
    )
    return CurrentStateObjectResponse(
        source_application_id=item.source_application_id,
        object_type=item.object_type,
        object_id=item.object_id,
        version=item.version,
        payload=item.payload,
        payload_contract_version=item.payload_contract_version,
        updated_at=item.updated_at,
    )


@router.get(
    "/objects/{source_application_id}/{object_type}/{object_id}/history",
    response_model=ChangeHistoryListResponse,
)
async def list_aggregated_object_history(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                DATA_READ_PERMISSION,
                application_parameter="source_application_id",
            )
        ),
    ],
    source_application_id: str,
    object_type: str,
    object_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChangeHistoryListResponse:
    try:
        allowed = merge_portal_scope(
            principal.application_scope(DATA_READ_PERMISSION),
            source_application_id,
        )
        page = await DataQueryService().list_history(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            object_id=object_id,
            allowed_application_ids=allowed,
            limit=limit,
            offset=offset,
        )
    except DataQueryValidationError as error:
        raise _map_validation(error) from error

    await _audit_success(
        request,
        session,
        principal,
        action="platform.data.read.history",
        source_application_id=source_application_id,
        object_type=object_type,
        object_id=object_id,
        detail={"total": page.total, "limit": limit, "offset": offset},
    )
    return ChangeHistoryListResponse(
        items=[
            ChangeHistoryRecordResponse(
                source_application_id=item.source_application_id,
                object_type=item.object_type,
                object_id=item.object_id,
                operation=item.operation,
                version=item.version,
                payload=item.payload,
                payload_contract_version=item.payload_contract_version,
                content_hash=item.content_hash,
                received_at=item.received_at,
                batch_id=item.batch_id,
            )
            for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
