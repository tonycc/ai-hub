from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ai_hub_platform import __version__

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    return HealthResponse(service="ai-hub-platform", version=__version__)
