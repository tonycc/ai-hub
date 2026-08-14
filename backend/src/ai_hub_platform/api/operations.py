from __future__ import annotations

from datetime import datetime
from secrets import compare_digest
from typing import Annotated, Literal, cast

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from ai_hub_platform.api.dependencies import SessionDependency, portal_permission_dependency
from ai_hub_platform.modules.operations.service import OperationsService
from ai_hub_platform.modules.portal.service import PortalPrincipal
from ai_hub_platform.operations.targets import ProductionTargets

router = APIRouter(prefix="/portal-api/v1", tags=["platform-operations"])
internal_router = APIRouter(prefix="/internal", tags=["internal"])
DiagnosticStatus = Literal["HEALTHY", "WARNING", "CRITICAL", "UNKNOWN", "DISABLED"]


def _production_targets(request: Request) -> ProductionTargets:
    return cast(ProductionTargets, request.app.state.production_targets)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApplicationEntryDiagnostic(ApiModel):
    application_id: str
    application_name: str
    environment: str
    portal_url: str
    health_url: str
    last_health_status: str | None
    last_health_checked_at: datetime | None
    environment_status: str
    status: DiagnosticStatus
    reason: str


class EventQueueDiagnostic(ApiModel):
    queue_name: str
    messages_ready: int
    messages_unacknowledged: int
    consumer_count: int
    status: DiagnosticStatus
    reason: str


class ProjectionDiagnostic(ApiModel):
    application_id: str
    application_name: str
    last_source_sequence: int
    last_snapshot_watermark: int
    updated_at: datetime
    open_gap_count: int
    checkpoint_age_seconds: int
    status: DiagnosticStatus
    reason: str


class OperationsSummaryResponse(ApiModel):
    observed_at: datetime
    overall_status: Literal["HEALTHY", "DEGRADED"]
    application_entries: list[ApplicationEntryDiagnostic]
    event_queues: list[EventQueueDiagnostic]
    projections: list[ProjectionDiagnostic]
    runbook_path: str


@router.get("/operations/summary", response_model=OperationsSummaryResponse)
async def operations_summary(
    request: Request,
    session: SessionDependency,
    principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.operations.read",
                application_parameter=None,
            )
        ),
    ],
) -> OperationsSummaryResponse:
    settings = request.app.state.settings
    targets = _production_targets(request)
    async with httpx.AsyncClient() as client:
        summary = await OperationsService().summary(
            session,
            visible_application_ids=principal.application_scope("platform.application.read"),
            rabbitmq_management_url=settings.operations_rabbitmq_management_url,
            rabbitmq_vhost=settings.operations_rabbitmq_vhost,
            rabbitmq_username=settings.operations_rabbitmq_username,
            rabbitmq_password=settings.operations_rabbitmq_password,
            http_client=client,
            event_backlog_warning=targets.slo.event_backlog_warning,
            event_backlog_critical=targets.slo.event_backlog_critical,
        )
    return OperationsSummaryResponse.model_validate(summary)


@internal_router.get(
    "/operations/summary",
    response_model=OperationsSummaryResponse,
    include_in_schema=False,
)
async def internal_operations_summary(
    request: Request,
    session: SessionDependency,
    monitor_token: Annotated[
        str | None,
        Header(alias="X-AI-Hub-Monitor-Token"),
    ] = None,
) -> OperationsSummaryResponse:
    settings = request.app.state.settings
    targets = _production_targets(request)
    if settings.monitor_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal monitoring is not configured",
        )
    if monitor_token is None or not compare_digest(
        monitor_token,
        settings.monitor_token.get_secret_value(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid monitor credential",
        )
    async with httpx.AsyncClient() as client:
        summary = await OperationsService().summary(
            session,
            visible_application_ids=None,
            rabbitmq_management_url=settings.operations_rabbitmq_management_url,
            rabbitmq_vhost=settings.operations_rabbitmq_vhost,
            rabbitmq_username=settings.operations_rabbitmq_username,
            rabbitmq_password=settings.operations_rabbitmq_password,
            http_client=client,
            event_backlog_warning=targets.slo.event_backlog_warning,
            event_backlog_critical=targets.slo.event_backlog_critical,
        )
    return OperationsSummaryResponse.model_validate(summary)
