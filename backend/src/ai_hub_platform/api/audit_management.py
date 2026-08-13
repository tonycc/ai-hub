from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ai_hub_platform.api.dependencies import SessionDependency, portal_permission_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditService
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1", tags=["audit-management"])

AuditResult = Literal["SUCCESS", "DENIED", "FAILED"]
REDACTED = "[REDACTED]"
SENSITIVE_KEY_NAMES = frozenset(
    {
        "authorization",
        "client_secret",
        "code_verifier",
        "cookie",
        "credential_secret",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "token",
    }
)
SENSITIVE_KEY_SUFFIXES = ("_password", "_private_key", "_secret", "_token")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditEventResponse(ApiModel):
    audit_id: UUID
    occurred_at: datetime
    request_id: str
    trace_id: str | None
    application_id: str | None
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    result: str
    error_code: str | None
    authorization_version: int | None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditEventListResponse(ApiModel):
    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


def sanitize_audit_value(value: object) -> object:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, nested in cast(dict[object, object], value).items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized in SENSITIVE_KEY_NAMES or normalized.endswith(SENSITIVE_KEY_SUFFIXES):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_audit_value(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize_audit_value(item) for item in cast(list[object], value)]
    return value


@router.get("/audit-events", response_model=AuditEventListResponse)
async def list_audit_events(
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.audit.read")),
    ],
    actor_id: Annotated[str | None, Query(max_length=255)] = None,
    action: Annotated[str | None, Query(max_length=200)] = None,
    result: AuditResult | None = None,
    request_id: Annotated[str | None, Query(max_length=128)] = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventListResponse:
    if occurred_from is not None and occurred_to is not None:
        if occurred_from >= occurred_to:
            raise ApiError(
                422,
                "invalid_audit_time_range",
                "occurred_from must be earlier than occurred_to",
            )
    rows, total = await AuditService().query(
        session,
        application_ids=principal.application_scope("platform.audit.read"),
        actor_id=actor_id,
        action=action,
        result=result,
        request_id=request_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        limit=limit,
        offset=offset,
    )
    items: list[AuditEventResponse] = []
    for row in rows:
        row["metadata"] = sanitize_audit_value(row.get("metadata", {}))
        items.append(AuditEventResponse.model_validate(row))
    return AuditEventListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
