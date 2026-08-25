from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ai_hub_platform import __version__
from ai_hub_platform.api.dependencies import SessionDependency

router = APIRouter(prefix="/health", tags=["health"])
internal_router = APIRouter(prefix="/internal", tags=["internal"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    service: str
    version: str
    bootstrap_reconciliation: str | None = None


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="ai-hub-platform", version=__version__)


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    request: Request,
    response: Response,
    session: SessionDependency,
) -> ReadyResponse:
    _ = await session.scalar(sa.text("SELECT 1"))
    reconciliation = getattr(request.app.state, "bootstrap_reconciliation", None)
    if reconciliation is None or reconciliation.status == "reconciled":
        return ReadyResponse(service="ai-hub-platform", version=__version__)
    # A deferred outcome only means the bootstrap seed row is absent/revoked.
    # If the environment already has a live credential bound to the dedicated
    # provider (e.g. after a normal rotation), the platform is fully usable and
    # must stay ready; only report 503 when no usable binding exists at all.
    settings = request.app.state.settings
    has_live_binding = await session.scalar(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM platform_core.application_credential
                WHERE application_id = :application_id
                  AND environment = :environment
                  AND status IN ('ACTIVE', 'DRAINING')
                  AND provider_external_id IS NOT NULL
            )
            """
        ),
        {
            "application_id": settings.sandbox_application_id,
            "environment": settings.environment,
        },
    )
    if has_live_binding:
        return ReadyResponse(service="ai-hub-platform", version=__version__)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(
        status="degraded",
        service="ai-hub-platform",
        version=__version__,
        bootstrap_reconciliation=f"{reconciliation.status}: {reconciliation.detail}",
    )


@internal_router.get(
    "/metrics",
    include_in_schema=False,
    response_class=PlainTextResponse,
)
async def metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        request.app.state.metrics_registry.render(),
        media_type="application/openmetrics-text; version=1.0.0",
    )
