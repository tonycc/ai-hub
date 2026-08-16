from __future__ import annotations

from datetime import datetime
from secrets import compare_digest
from typing import Annotated, Literal, cast

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


class ServiceWindowTargetsResponse(ApiModel):
    days: list[str]
    start: str
    end: str
    planned_maintenance_notice_hours: int


class DeploymentTargetsResponse(ApiModel):
    tier: str
    profile: str
    topology: str
    off_host_backup_required: bool


class SloTargetsResponse(ApiModel):
    monthly_availability_percent: float
    public_api_p95_ms: float
    public_api_p99_ms: float
    minimum_test_rps: float
    minimum_test_requests: int
    maximum_server_error_percent: float


class RecoveryTargetsResponse(ApiModel):
    rpo_minutes: int
    rto_minutes: int
    backup_interval_minutes: int


class RetentionTargetsResponse(ApiModel):
    audit_days: int
    notification_days: int
    portal_session_days_after_expiry: int
    conformance_days_after_expiry: int
    backup_hourly_count: int
    backup_daily_days: int


class AlertRouteTargetsResponse(ApiModel):
    route_key: str
    primary: str
    backup: str
    acknowledge_minutes: int


class HaUpgradeTriggersResponse(ApiModel):
    availability_percent: float
    rpo_minutes: int
    rto_minutes: int
    sustained_rps: float


class ProductionTargetsResponse(ApiModel):
    schema_version: int
    configuration_mode: Literal["CONFIG_AS_CODE"]
    editable: Literal[False]
    source: Literal["deploy/operations/production-targets.json"]
    timezone: str
    service_window: ServiceWindowTargetsResponse
    deployment: DeploymentTargetsResponse
    slo: SloTargetsResponse
    recovery: RecoveryTargetsResponse
    retention: RetentionTargetsResponse
    alert_routes: list[AlertRouteTargetsResponse]
    ha_upgrade_triggers: HaUpgradeTriggersResponse


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


class OperationsSummaryResponse(ApiModel):
    observed_at: datetime
    overall_status: Literal["HEALTHY", "DEGRADED"]
    application_entries: list[ApplicationEntryDiagnostic]
    runbook_path: str


def production_targets_response(
    targets: ProductionTargets,
) -> ProductionTargetsResponse:
    return ProductionTargetsResponse.model_validate(
        {
            "schema_version": targets.schema_version,
            "configuration_mode": "CONFIG_AS_CODE",
            "editable": False,
            "source": "deploy/operations/production-targets.json",
            "timezone": targets.timezone,
            "service_window": {
                "days": list(targets.service_window.days),
                "start": targets.service_window.start,
                "end": targets.service_window.end,
                "planned_maintenance_notice_hours": (
                    targets.service_window.planned_maintenance_notice_hours
                ),
            },
            "deployment": {
                "tier": targets.deployment_tier,
                "profile": targets.profile,
                "topology": targets.deployment_topology,
                "off_host_backup_required": targets.off_host_backup_required,
            },
            "slo": {
                "monthly_availability_percent": (targets.slo.monthly_availability_percent),
                "public_api_p95_ms": targets.slo.public_api_p95_ms,
                "public_api_p99_ms": targets.slo.public_api_p99_ms,
                "minimum_test_rps": targets.slo.minimum_test_rps,
                "minimum_test_requests": targets.slo.minimum_test_requests,
                "maximum_server_error_percent": (targets.slo.maximum_server_error_percent),
            },
            "recovery": {
                "rpo_minutes": targets.recovery.rpo_minutes,
                "rto_minutes": targets.recovery.rto_minutes,
                "backup_interval_minutes": targets.recovery.backup_interval_minutes,
            },
            "retention": {
                "audit_days": targets.retention.audit_days,
                "notification_days": targets.retention.notification_days,
                "portal_session_days_after_expiry": (
                    targets.retention.portal_session_days_after_expiry
                ),
                "conformance_days_after_expiry": (targets.retention.conformance_days_after_expiry),
                "backup_hourly_count": targets.retention.backup_hourly_count,
                "backup_daily_days": targets.retention.backup_daily_days,
            },
            "alert_routes": [
                {
                    "route_key": route.route_key,
                    "primary": route.primary,
                    "backup": route.backup,
                    "acknowledge_minutes": route.acknowledge_minutes,
                }
                for route in targets.alert_routes
            ],
            "ha_upgrade_triggers": {
                "availability_percent": (targets.ha_upgrade_triggers.availability_percent),
                "rpo_minutes": targets.ha_upgrade_triggers.rpo_minutes,
                "rto_minutes": targets.ha_upgrade_triggers.rto_minutes,
                "sustained_rps": targets.ha_upgrade_triggers.sustained_rps,
            },
        }
    )


@router.get("/operations/targets", response_model=ProductionTargetsResponse)
async def operations_targets(
    request: Request,
    _principal: Annotated[
        PortalPrincipal,
        Depends(
            portal_permission_dependency(
                "platform.operations.read",
                application_parameter=None,
            )
        ),
    ],
) -> ProductionTargetsResponse:
    return production_targets_response(_production_targets(request))


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
    summary = await OperationsService().summary(
        session,
        visible_application_ids=principal.application_scope("platform.application.read"),
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
    summary = await OperationsService().summary(
        session,
        visible_application_ids=None,
    )
    return OperationsSummaryResponse.model_validate(summary)
