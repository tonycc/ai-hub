from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_hub_platform.api.dependencies import SessionDependency, portal_permission_dependency
from ai_hub_platform.api.errors import ApiError
from ai_hub_platform.modules.audit.service import AuditRecord, AuditService
from ai_hub_platform.modules.conformance.service import (
    ALL_PROFILES,
    ConformanceNotFoundError,
    ConformanceProfile,
    ConformanceService,
    ConformanceValidationError,
)
from ai_hub_platform.modules.portal.service import PortalPrincipal

router = APIRouter(prefix="/portal-api/v1", tags=["integration-conformance"])

RunStatus = Literal["RUNNING", "PASSED", "FAILED"]
CheckStatus = Literal["PASSED", "FAILED", "NOT_APPLICABLE"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConformanceCheckResponse(ApiModel):
    profile: ConformanceProfile
    status: CheckStatus
    message: str
    evidence: dict[str, Any]


class ConformanceRunSummaryResponse(ApiModel):
    run_id: UUID
    application_id: str
    application_name: str | None = None
    environment: str
    requested_by_user_id: UUID | None
    requested_by_name: str | None = None
    status: RunStatus
    contract_version: str
    requested_profiles: list[ConformanceProfile]
    summary: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None


class ConformanceRunResponse(ConformanceRunSummaryResponse):
    checks: list[ConformanceCheckResponse]


class ConformanceRunListResponse(ApiModel):
    items: list[ConformanceRunSummaryResponse]
    total: int
    limit: int
    offset: int


class ConformanceRunRequest(ApiModel):
    environment: str = Field(min_length=1, max_length=32)
    profiles: list[ConformanceProfile] = Field(
        default_factory=lambda: list(ALL_PROFILES),
        min_length=1,
        max_length=4,
    )

    @field_validator("profiles")
    @classmethod
    def unique_profiles(cls, value: list[ConformanceProfile]) -> list[ConformanceProfile]:
        if len(value) != len(set(value)):
            raise ValueError("Conformance profiles must be unique")
        return [profile for profile in ALL_PROFILES if profile in value]


@router.get("/conformance-runs", response_model=ConformanceRunListResponse)
async def list_conformance_runs(
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.application.read")),
    ],
    application_id: Annotated[str | None, Query(max_length=63)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConformanceRunListResponse:
    visible = principal.application_scope("platform.application.read")
    if application_id is not None and visible is not None and application_id not in visible:
        raise ApiError(
            403,
            "platform_permission_denied",
            "Platform role cannot view conformance for this application",
        )
    rows, total = await ConformanceService().list_runs(
        session,
        visible_application_ids=visible,
        application_id=application_id,
        limit=limit,
        offset=offset,
    )
    return ConformanceRunListResponse(
        items=[ConformanceRunSummaryResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/conformance-runs/{run_id}", response_model=ConformanceRunResponse)
async def get_conformance_run(
    run_id: UUID,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(portal_permission_dependency("platform.application.read")),
    ],
) -> ConformanceRunResponse:
    try:
        row = await ConformanceService().get_run(
            session,
            run_id=run_id,
            visible_application_ids=principal.application_scope("platform.application.read"),
        )
    except ConformanceNotFoundError as error:
        raise ApiError(404, "conformance_run_not_found", str(error)) from error
    return ConformanceRunResponse.model_validate(row)


@router.post(
    "/applications/{application_id}/conformance-runs",
    response_model=ConformanceRunResponse,
    status_code=201,
)
async def run_conformance(
    application_id: str,
    payload: ConformanceRunRequest,
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.conformance.run",
                require_csrf=True,
            )
        ),
    ],
) -> ConformanceRunResponse:
    try:
        row = await ConformanceService().run(
            session,
            application_id=application_id,
            environment=payload.environment,
            profiles=payload.profiles,
            requested_by_user_id=principal.user_id,
        )
    except ConformanceNotFoundError as error:
        raise ApiError(404, "conformance_resource_not_found", str(error)) from error
    except ConformanceValidationError as error:
        raise ApiError(422, "conformance_validation_failed", str(error)) from error
    await AuditService().append(
        session,
        AuditRecord(
            request_id=str(request.state.request_id),
            trace_id=getattr(request.state, "trace_id", None),
            action="platform.conformance.run",
            result="SUCCESS" if row["status"] == "PASSED" else "FAILED",
            actor_type="user",
            actor_id=principal.subject,
            application_id=application_id,
            target_type="conformance_run",
            target_id=str(row["run_id"]),
            authorization_version=principal.authorization_version,
            metadata={
                "contract_version": row["contract_version"],
                "requested_profiles": list(row["requested_profiles"]),
            },
        ),
    )
    return ConformanceRunResponse.model_validate(row)
