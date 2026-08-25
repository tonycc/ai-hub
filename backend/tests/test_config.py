import os

import pytest
from ai_hub_platform.config import (
    CoreMigrationSettings,
    Settings,
)
from pydantic import SecretStr, ValidationError


def test_local_runtime_defaults_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("AI_HUB_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.environment == "local"
    assert settings.application_id == "ai-hub-platform"
    assert settings.oidc_issuer.startswith("http://localhost:")


def test_production_rejects_local_runtime_defaults() -> None:
    with pytest.raises(ValidationError, match="database_url cannot use a local hostname"):
        Settings(environment="production")


def test_production_runtime_accepts_secure_non_local_configuration() -> None:
    settings = Settings(
        environment="production",
        database_url=(
            "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
            "platform-db.internal:5432/platform_db"
        ),
        oidc_issuer="https://identity.example.org/application/o/ai-hub/",
        portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
        portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
        portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
        authentik_api_url="https://identity.example.org/api/v3",
        authentik_external_url="https://identity.example.org",
        authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
        monitor_token=SecretStr("ProdMonitorToken-4821"),
        public_platform_base_url="https://platform.example.org",
        public_identity_base_url="https://identity.example.org",
        raw_database_url=(
            "postgresql+psycopg://ai_hub_raw:ProdRawSecret-6754@"
            "platform-db.internal:5432/platform_db"
        ),
        oidc_client_secret=SecretStr("ProdOidcClientSecret-1123"),
        credential_rotation_overlap_seconds=300,
        standalone_portal_url="https://app.example.org/",
        standalone_api_base_url="https://app.example.org",
        standalone_health_url="https://app.example.org/health",
        standalone_oidc_redirect_uri="https://app.example.org/auth/callback",
    )

    assert settings.environment == "production"


def test_production_accepts_cluster_internal_http_authentik_api_url() -> None:
    settings = Settings(
        environment="production",
        database_url=(
            "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
            "platform-db.internal:5432/platform_db"
        ),
        oidc_issuer="https://identity.example.org/application/o/ai-hub/",
        portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
        portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
        portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
        authentik_api_url="http://authentik-server:9000/api/v3",
        authentik_external_url="https://identity.example.org",
        authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
        monitor_token=SecretStr("ProdMonitorToken-4821"),
        public_platform_base_url="https://platform.example.org",
        public_identity_base_url="https://identity.example.org",
        raw_database_url=(
            "postgresql+psycopg://ai_hub_raw:ProdRawSecret-6754@"
            "platform-db.internal:5432/platform_db"
        ),
        oidc_client_secret=SecretStr("ProdOidcClientSecret-1123"),
        credential_rotation_overlap_seconds=300,
        standalone_portal_url="https://app.example.org/",
        standalone_api_base_url="https://app.example.org",
        standalone_health_url="https://app.example.org/health",
        standalone_oidc_redirect_uri="https://app.example.org/auth/callback",
    )
    assert settings.authentik_api_url == "http://authentik-server:9000/api/v3"


def test_production_rejects_public_http_authentik_api_url() -> None:
    with pytest.raises(ValidationError, match="authentik_api_url must use https"):
        Settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
            portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
            portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
            authentik_api_url="http://identity.example.org/api/v3",
            authentik_external_url="https://identity.example.org",
            authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
            monitor_token=SecretStr("ProdMonitorToken-4821"),
            public_platform_base_url="https://platform.example.org",
            public_identity_base_url="https://identity.example.org",
            raw_database_url=(
                "postgresql+psycopg://ai_hub_raw:ProdRawSecret-6754@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_client_secret=SecretStr("ProdOidcClientSecret-1123"),
            credential_rotation_overlap_seconds=300,
            standalone_portal_url="https://app.example.org/",
            standalone_api_base_url="https://app.example.org",
            standalone_health_url="https://app.example.org/health",
            standalone_oidc_redirect_uri="https://app.example.org/auth/callback",
        )


def test_production_rejects_prefix_spoofed_internal_hostname() -> None:
    with pytest.raises(ValidationError, match="authentik_api_url must use https"):
        Settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
            portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
            portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
            authentik_api_url="http://authentik-server.evil.example/api/v3",
            authentik_external_url="https://identity.example.org",
            authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
            monitor_token=SecretStr("ProdMonitorToken-4821"),
            public_platform_base_url="https://platform.example.org",
            public_identity_base_url="https://identity.example.org",
            raw_database_url=(
                "postgresql+psycopg://ai_hub_raw:ProdRawSecret-6754@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_client_secret=SecretStr("ProdOidcClientSecret-1123"),
            credential_rotation_overlap_seconds=300,
            standalone_portal_url="https://app.example.org/",
            standalone_api_base_url="https://app.example.org",
            standalone_health_url="https://app.example.org/health",
            standalone_oidc_redirect_uri="https://app.example.org/auth/callback",
        )


def test_database_url_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="must include a username and password"):
        Settings(
            environment="test",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:@platform-db.test:5432/platform_db"
            ),
        )


def test_production_requires_https_oidc_issuer() -> None:
    with pytest.raises(ValidationError, match="oidc_issuer must use https"):
        Settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_issuer="http://identity.example.org/application/o/ai-hub/",
            portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
            portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
            portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
            authentik_api_url="https://identity.example.org/api/v3",
            authentik_external_url="https://identity.example.org",
            authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
        )


def test_validation_error_does_not_expose_database_password() -> None:
    exposed_password = "change-me-sensitive-value"

    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            database_url=(
                f"postgresql+psycopg://ai_hub_platform:{exposed_password}@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
            portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
            portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
            authentik_api_url="https://identity.example.org/api/v3",
            authentik_external_url="https://identity.example.org",
            authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
        )

    assert exposed_password not in str(error.value)


def test_production_rejects_placeholder_portal_client_secret() -> None:
    with pytest.raises(
        ValidationError,
        match="portal_oidc_client_secret cannot use a placeholder",
    ):
        Settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
            portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
            portal_oidc_client_secret=SecretStr("local-only-portal-secret"),
            authentik_api_url="https://identity.example.org/api/v3",
            authentik_external_url="https://identity.example.org",
            authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
        )


def test_production_rejects_placeholder_authentik_api_token() -> None:
    with pytest.raises(
        ValidationError,
        match="authentik_api_token cannot use a placeholder",
    ):
        Settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            portal_oidc_issuer=("https://identity.example.org/application/o/ai-hub-portal/"),
            portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
            portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
            authentik_api_url="https://identity.example.org/api/v3",
            authentik_external_url="https://identity.example.org",
            authentik_api_token=SecretStr("local-only-authentik-token"),
        )


def test_production_rejects_placeholder_monitor_token() -> None:
    with pytest.raises(ValidationError, match="monitor_token cannot use a placeholder"):
        Settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:ProdSecret-8374@"
                "platform-db.internal:5432/platform_db"
            ),
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            portal_oidc_issuer="https://identity.example.org/application/o/ai-hub-portal/",
            portal_oidc_redirect_uri="https://platform.example.org/auth/callback",
            portal_oidc_client_secret=SecretStr("ProdPortalSecret-9482"),
            authentik_api_url="https://identity.example.org/api/v3",
            authentik_external_url="https://identity.example.org",
            authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
            monitor_token=SecretStr("local-only-monitor-token"),
            public_platform_base_url="https://platform.example.org",
            public_identity_base_url="https://identity.example.org",
            credential_rotation_overlap_seconds=300,
            standalone_portal_url="https://app.example.org/",
            standalone_api_base_url="https://app.example.org",
            standalone_health_url="https://app.example.org/health",
            standalone_oidc_redirect_uri="https://app.example.org/auth/callback",
        )


def test_migration_processes_validate_only_their_own_database_url() -> None:
    core = CoreMigrationSettings(
        environment="production",
        migration_database_url=(
            "postgresql+psycopg://ai_hub_platform_migrator:CoreSecret-3819@"
            "platform-db.internal:5432/platform_db"
        ),
    )
    from ai_hub_platform.config import RawMigrationSettings

    raw = RawMigrationSettings(
        environment="production",
        raw_migration_database_url=(
            "postgresql+psycopg://ai_hub_raw_migrator:RawSecret-1847@"
            "platform-db.internal:5432/platform_db"
        ),
    )

    assert core.environment == "production"
    assert raw.environment == "production"
