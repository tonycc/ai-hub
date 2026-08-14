from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Request
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


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="ai-hub-platform", version=__version__)


@router.get("/ready", response_model=HealthResponse)
async def ready(session: SessionDependency) -> HealthResponse:
    _ = await session.scalar(sa.text("SELECT 1"))
    return HealthResponse(service="ai-hub-platform", version=__version__)


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
