from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from ai_hub_sdk import OidcTokenValidator
from fastapi import FastAPI

from ai_hub_platform import __version__
from ai_hub_platform.api.errors import register_error_handlers
from ai_hub_platform.api.health import router as health_router
from ai_hub_platform.api.platform import router as platform_router
from ai_hub_platform.config import Settings, get_settings
from ai_hub_platform.modules.permission.service import PermissionService
from ai_hub_platform.shared.database import Database
from ai_hub_platform.shared.observability import RequestContextMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        database = Database(resolved_settings.database_url)
        token_validator = OidcTokenValidator(
            resolved_settings.oidc_issuer,
            resolved_settings.oidc_audience,
            cache_ttl_seconds=resolved_settings.oidc_jwks_cache_ttl_seconds,
            stale_ttl_seconds=resolved_settings.oidc_jwks_stale_ttl_seconds,
        )
        app.state.database = database
        app.state.token_validator = token_validator
        app.state.permission_service = PermissionService(
            resolved_settings.authorization_cache_ttl_seconds
        )
        app.state.settings = resolved_settings
        yield
        await token_validator.close()
        await database.dispose()

    application = FastAPI(
        title="AI Hub Platform API",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(RequestContextMiddleware)
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(platform_router)
    return application


app = create_app()
