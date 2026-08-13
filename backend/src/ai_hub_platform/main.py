from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from ai_hub_sdk import OidcClient, OidcTokenValidator
from fastapi import FastAPI

from ai_hub_platform import __version__
from ai_hub_platform.api.application_management import (
    router as application_management_router,
)
from ai_hub_platform.api.audit_management import router as audit_management_router
from ai_hub_platform.api.conformance import router as conformance_router
from ai_hub_platform.api.developer import router as developer_router
from ai_hub_platform.api.errors import register_error_handlers
from ai_hub_platform.api.governance import router as governance_router
from ai_hub_platform.api.health import router as health_router
from ai_hub_platform.api.notification_management import (
    router as notification_management_router,
)
from ai_hub_platform.api.operations import router as operations_router
from ai_hub_platform.api.platform import router as platform_router
from ai_hub_platform.api.portal_auth import auth_router, session_router
from ai_hub_platform.config import Settings, get_settings
from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.modules.permission.service import PermissionService
from ai_hub_platform.shared.database import Database
from ai_hub_platform.shared.observability import (
    PortalAuditMiddleware,
    RequestContextMiddleware,
)


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
        portal_oidc_client = OidcClient(
            resolved_settings.portal_oidc_issuer,
            resolved_settings.portal_oidc_client_id,
            resolved_settings.portal_oidc_client_secret.get_secret_value(),
        )
        portal_token_validator = OidcTokenValidator(
            resolved_settings.portal_oidc_issuer,
            resolved_settings.portal_oidc_audience,
            cache_ttl_seconds=resolved_settings.oidc_jwks_cache_ttl_seconds,
            stale_ttl_seconds=resolved_settings.oidc_jwks_stale_ttl_seconds,
        )
        authentik_admin_client = AuthentikAdminClient(
            resolved_settings.authentik_api_url,
            resolved_settings.authentik_api_token.get_secret_value(),
            resolved_settings.authentik_external_url,
            resolved_settings.authentik_provider_template_client_id,
        )
        app.state.database = database
        app.state.token_validator = token_validator
        app.state.portal_oidc_client = portal_oidc_client
        app.state.portal_token_validator = portal_token_validator
        app.state.authentik_admin_client = authentik_admin_client
        app.state.permission_service = PermissionService(
            resolved_settings.authorization_cache_ttl_seconds
        )
        app.state.settings = resolved_settings
        yield
        await authentik_admin_client.close()
        await portal_oidc_client.close()
        await portal_token_validator.close()
        await token_validator.close()
        await database.dispose()

    application = FastAPI(
        title="AI Hub Platform API",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(PortalAuditMiddleware)
    application.add_middleware(RequestContextMiddleware)
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(platform_router)
    application.include_router(auth_router)
    application.include_router(session_router)
    application.include_router(governance_router)
    application.include_router(application_management_router)
    application.include_router(notification_management_router)
    application.include_router(audit_management_router)
    application.include_router(developer_router)
    application.include_router(conformance_router)
    application.include_router(operations_router)
    return application


app = create_app()
