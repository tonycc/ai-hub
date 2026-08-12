from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal, cast

from ai_hub_sdk import AiHubClient
from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel

from standalone_app import __version__
from standalone_app.config import Settings, get_settings


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class PlatformStatusResponse(BaseModel):
    status: Literal["ok"]
    platform_service: str
    platform_version: str


router = APIRouter()


def get_platform_client(request: Request) -> AiHubClient:
    return cast(AiHubClient, request.app.state.platform_client)


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
async def live() -> HealthResponse:
    return HealthResponse(service="standalone-example", version=__version__)


@router.get("/api/v1/platform-status", response_model=PlatformStatusResponse, tags=["platform"])
async def platform_status(request: Request) -> PlatformStatusResponse:
    health = await get_platform_client(request).health()
    return PlatformStatusResponse(
        status=health.status,
        platform_service=health.service,
        platform_version=health.version,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
        platform_client = AiHubClient(resolved_settings.platform_api_base_url)
        application.state.platform_client = platform_client
        application.state.settings = resolved_settings
        yield
        await platform_client.close()

    application = FastAPI(
        title="AI Hub Standalone Application Example",
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(router)
    return application


app = create_app()
