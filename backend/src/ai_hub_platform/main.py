from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_hub_platform import __version__
from ai_hub_platform.api.health import router as health_router
from ai_hub_platform.config import Settings, get_settings
from ai_hub_platform.shared.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        database = Database(resolved_settings.database_url)
        app.state.database = database
        app.state.settings = resolved_settings
        yield
        await database.dispose()

    application = FastAPI(
        title="AI Hub Platform API",
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(health_router)
    return application


app = create_app()
