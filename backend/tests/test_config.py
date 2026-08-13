import pytest
from ai_hub_platform.config import (
    CoreMigrationSettings,
    ProjectionMigrationSettings,
    Settings,
)
from pydantic import SecretStr, ValidationError


def test_local_runtime_defaults_are_valid() -> None:
    settings = Settings()

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
        public_platform_base_url="https://platform.example.org",
        public_identity_base_url="https://identity.example.org",
    )

    assert settings.environment == "production"


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


def test_migration_processes_validate_only_their_own_database_url() -> None:
    core = CoreMigrationSettings(
        environment="production",
        migration_database_url=(
            "postgresql+psycopg://ai_hub_platform_migrator:CoreSecret-3819@"
            "platform-db.internal:5432/platform_db"
        ),
    )
    projection = ProjectionMigrationSettings(
        environment="production",
        projection_migration_database_url=(
            "postgresql+psycopg://ai_hub_projection_migrator:ProjectionSecret-2947@"
            "platform-db.internal:5432/platform_db"
        ),
    )

    assert core.environment == "production"
    assert projection.environment == "production"


def test_operations_rabbitmq_observer_configuration_is_all_or_nothing() -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        Settings(
            environment="test",
            operations_rabbitmq_management_url="http://rabbitmq.test:15672",
        )


def test_production_rejects_placeholder_operations_observer_password() -> None:
    with pytest.raises(
        ValidationError,
        match="operations_rabbitmq_password cannot use a placeholder",
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
            authentik_api_token=SecretStr("ProdAuthentikApiToken-2938"),
            public_platform_base_url="https://platform.example.org",
            public_identity_base_url="https://identity.example.org",
            operations_rabbitmq_management_url="https://rabbitmq.example.org",
            operations_rabbitmq_username="platform_observer",
            operations_rabbitmq_password=SecretStr("local-only-observer-password"),
        )
