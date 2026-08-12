from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter
from pydantic import BaseModel

from ai_hub_platform import __version__
from ai_hub_platform.api.dependencies import SessionDependency

router = APIRouter(prefix="/health", tags=["health"])


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
