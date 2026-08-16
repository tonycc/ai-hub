import os

import pytest
from pydantic import ValidationError
from standalone_app.config import MigrationSettings, Settings


def test_local_runtime_defaults_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("STANDALONE_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)

    assert settings.environment == "local"
    assert settings.application_id == "standalone-example"
    assert settings.platform_api_base_url == "http://localhost:8000"


def test_production_rejects_local_platform_api_url() -> None:
    with pytest.raises(ValidationError, match="must use https outside local/test"):
        Settings(
            environment="production",
            database_url=(
                "postgresql+psycopg://standalone_app:ProdSecret-8374@"
                "standalone-db.internal:5432/standalone_app_db"
            ),
        )


def test_production_runtime_accepts_secure_non_local_configuration() -> None:
    settings = Settings(
        environment="production",
        platform_api_base_url="https://platform.example.org",
        oidc_issuer="https://identity.example.org/application/o/ai-hub/",
        oidc_client_secret="ProdOidcSecret-8374",
        session_secret="ProdSessionSecret-8374-LongEnough-ForSigning",
        database_url=(
            "postgresql+psycopg://standalone_app:ProdSecret-8374@"
            "standalone-db.internal:5432/standalone_app_db"
        ),
    )

    assert settings.environment == "production"


def test_production_rejects_placeholder_database_password() -> None:
    with pytest.raises(ValidationError, match="cannot use a placeholder password"):
        Settings(
            environment="production",
            platform_api_base_url="https://platform.example.org",
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            oidc_client_secret="ProdOidcSecret-8374",
            session_secret="ProdSessionSecret-8374-LongEnough-ForSigning",
            database_url=(
                "postgresql+psycopg://standalone_app:change-me-password@"
                "standalone-db.internal:5432/standalone_app_db"
            ),
        )


def test_platform_api_url_rejects_embedded_credentials() -> None:
    with pytest.raises(ValidationError, match="cannot include credentials"):
        Settings(
            environment="test",
            platform_api_base_url="http://user:password@platform.test",
        )


def test_validation_error_does_not_expose_database_password() -> None:
    exposed_password = "change-me-sensitive-value"

    with pytest.raises(ValidationError) as error:
        Settings(
            environment="production",
            platform_api_base_url="https://platform.example.org",
            oidc_issuer="https://identity.example.org/application/o/ai-hub/",
            oidc_client_secret="ProdOidcSecret-8374",
            session_secret="ProdSessionSecret-8374-LongEnough-ForSigning",
            database_url=(
                f"postgresql+psycopg://standalone_app:{exposed_password}@"
                "standalone-db.internal:5432/standalone_app_db"
            ),
        )

    assert exposed_password not in str(error.value)


def test_migration_process_does_not_require_platform_api_configuration() -> None:
    settings = MigrationSettings(
        environment="production",
        migration_database_url=(
            "postgresql+psycopg://standalone_app_migrator:MigrationSecret-3819@"
            "standalone-db.internal:5432/standalone_app_db"
        ),
    )

    assert settings.environment == "production"


def test_data_ingest_capability_is_accepted() -> None:
    settings = Settings(integration_capabilities="API_CLIENT,DATA_INGEST")

    assert "DATA_INGEST" in settings.capabilities


def test_unknown_capability_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unsupported capability"):
        Settings(integration_capabilities="API_CLIENT,EVENT_PUBLISHER")


def test_jwks_stale_window_cannot_be_shorter_than_fresh_cache() -> None:
    with pytest.raises(ValidationError, match="must not be shorter"):
        Settings(
            environment="test",
            oidc_jwks_cache_ttl_seconds=60,
            oidc_jwks_stale_ttl_seconds=30,
        )
