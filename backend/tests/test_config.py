import pytest
from ai_hub_platform.config import (
    CoreMigrationSettings,
    ProjectionMigrationSettings,
    Settings,
)
from pydantic import ValidationError


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
    )

    assert settings.environment == "production"


def test_database_url_requires_credentials() -> None:
    with pytest.raises(ValidationError, match="must include a username and password"):
        Settings(
            environment="test",
            database_url=(
                "postgresql+psycopg://ai_hub_platform:@platform-db.test:5432/"
                "platform_db"
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
        )

    assert exposed_password not in str(error.value)


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
