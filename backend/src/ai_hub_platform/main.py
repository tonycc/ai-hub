import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from ai_hub_sdk import OidcClient, OidcTokenValidator
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_hub_platform import __version__
from ai_hub_platform.api.application_management import (
    router as application_management_router,
)
from ai_hub_platform.api.audit_management import router as audit_management_router
from ai_hub_platform.api.conformance import router as conformance_router
from ai_hub_platform.api.data import router as data_router
from ai_hub_platform.api.data_governance import router as data_governance_router
from ai_hub_platform.api.developer import router as developer_router
from ai_hub_platform.api.errors import register_error_handlers
from ai_hub_platform.api.governance import router as governance_router
from ai_hub_platform.api.health import internal_router
from ai_hub_platform.api.health import router as health_router
from ai_hub_platform.api.ingest import router as ingest_router
from ai_hub_platform.api.notification_management import (
    router as notification_management_router,
)
from ai_hub_platform.api.operations import internal_router as internal_operations_router
from ai_hub_platform.api.operations import router as operations_router
from ai_hub_platform.api.platform import router as platform_router
from ai_hub_platform.api.portal_auth import auth_router, session_router
from ai_hub_platform.config import Settings, get_settings
from ai_hub_platform.modules.app_management.authentik import AuthentikAdminClient
from ai_hub_platform.modules.app_management.bootstrap import (
    start_bootstrap_reconciliation,
)
from ai_hub_platform.modules.governance.version_sync import (
    start_authorization_version_reconciler,
)
from ai_hub_platform.modules.permission.service import PermissionService
from ai_hub_platform.operations.targets import load_production_targets
from ai_hub_platform.shared.database import Database
from ai_hub_platform.shared.metrics import MetricsMiddleware, MetricsRegistry
from ai_hub_platform.shared.observability import (
    PortalAuditMiddleware,
    RequestContextMiddleware,
)
from ai_hub_platform.shared.token_validation import RegisteredOidcTokenValidator

LOGGER = logging.getLogger(__name__)

__all__ = ["create_app"]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    production_targets = load_production_targets(
        resolved_settings.production_targets_path
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        database = Database(resolved_settings.database_url)
        primary_token_validator = OidcTokenValidator(
            resolved_settings.oidc_issuer,
            resolved_settings.oidc_audience,
            cache_ttl_seconds=resolved_settings.oidc_jwks_cache_ttl_seconds,
            stale_ttl_seconds=resolved_settings.oidc_jwks_stale_ttl_seconds,
        )
        token_validator = RegisteredOidcTokenValidator(
            primary_token_validator,
            database,
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
        raw_engine = create_async_engine(
            resolved_settings.raw_database_url, pool_pre_ping=True
        )
        app.state.raw_sessions = async_sessionmaker(
            raw_engine, class_=AsyncSession, expire_on_commit=False
        )
        reconciliation_state, reconciliation_task = start_bootstrap_reconciliation(
            database,
            authentik_admin_client,
            application_id=resolved_settings.sandbox_application_id,
            environment=resolved_settings.environment,
            external_url=resolved_settings.authentik_external_url,
            standalone_portal_url=resolved_settings.standalone_portal_url,
            standalone_api_base_url=resolved_settings.standalone_api_base_url,
            standalone_health_url=resolved_settings.standalone_health_url,
            standalone_oidc_redirect_uri=resolved_settings.standalone_oidc_redirect_uri,
        )
        app.state.bootstrap_reconciliation = reconciliation_state
        version_sync_task = start_authorization_version_reconciler(
            database, authentik_admin_client
        )
        yield
        version_sync_task.cancel()
        reconciliation_task.cancel()
        await raw_engine.dispose()
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
    metrics_registry = MetricsRegistry(
        service=resolved_settings.service_name,
        version=__version__,
    )
    application.state.metrics_registry = metrics_registry
    application.state.production_targets = production_targets
    application.add_middleware(PortalAuditMiddleware)
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(MetricsMiddleware, registry=metrics_registry)
    register_error_handlers(application)
    application.include_router(health_router)
    application.include_router(internal_router)
    application.include_router(internal_operations_router)
    application.include_router(platform_router)
    application.include_router(data_router)
    application.include_router(auth_router)
    application.include_router(session_router)
    application.include_router(governance_router)
    application.include_router(data_governance_router)
    application.include_router(application_management_router)
    application.include_router(notification_management_router)
    application.include_router(audit_management_router)
    application.include_router(developer_router)
    application.include_router(conformance_router)
    application.include_router(operations_router)
    application.include_router(ingest_router)
    return application


app = create_app()
