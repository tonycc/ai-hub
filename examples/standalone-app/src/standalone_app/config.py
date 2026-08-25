from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import SplitResult, unquote, urlsplit

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "integration", "uat", "production"]

_APPLICATION_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}
_PLACEHOLDER_MARKERS = (
    "change-me",
    "example",
    "local-",
    "local-only",
    "replace-me",
)
_STRICT_ENVIRONMENTS = {"integration", "uat", "production"}


def _is_strict_environment(environment: Environment) -> bool:
    return environment in _STRICT_ENVIRONMENTS


def _parse_url(
    value: str,
    *,
    field_name: str,
    allowed_schemes: set[str],
) -> SplitResult:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} must be a valid URL") from error

    if parsed.scheme not in allowed_schemes:
        schemes = ", ".join(sorted(allowed_schemes))
        raise ValueError(f"{field_name} must use one of these schemes: {schemes}")
    if hostname is None:
        raise ValueError(f"{field_name} must include a hostname")
    return parsed


def _is_local_hostname(hostname: str) -> bool:
    normalized = hostname.lower()
    return normalized in _LOCAL_HOSTS or normalized.endswith(".localhost")


def _has_placeholder_secret(secret: str) -> bool:
    normalized = secret.lower()
    return any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


def _validate_application_id(application_id: str) -> None:
    if _APPLICATION_ID_PATTERN.fullmatch(application_id) is None:
        raise ValueError(
            "application_id must contain 3-63 lowercase letters, numbers, or hyphens"
        )


def _validate_database_url(
    value: str,
    *,
    field_name: str,
    strict: bool,
) -> None:
    parsed = _parse_url(
        value,
        field_name=field_name,
        allowed_schemes={"postgresql+psycopg"},
    )
    if not parsed.username or not parsed.password:
        raise ValueError(f"{field_name} must include a username and password")
    if not parsed.path.strip("/"):
        raise ValueError(f"{field_name} must include a database name")
    if strict and _is_local_hostname(parsed.hostname or ""):
        raise ValueError(f"{field_name} cannot use a local hostname outside local/test")
    if strict and _has_placeholder_secret(unquote(parsed.password)):
        raise ValueError(f"{field_name} cannot use a placeholder password outside local/test")


def _validate_platform_api_base_url(value: str, *, strict: bool) -> None:
    parsed = _parse_url(
        value,
        field_name="platform_api_base_url",
        allowed_schemes={"http", "https"},
    )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("platform_api_base_url cannot include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("platform_api_base_url cannot include a query or fragment")
    if strict and parsed.scheme != "https":
        raise ValueError("platform_api_base_url must use https outside local/test")
    if strict and _is_local_hostname(parsed.hostname or ""):
        raise ValueError(
            "platform_api_base_url cannot use a local hostname outside local/test"
        )


def _validate_oidc_issuer(value: str, *, strict: bool) -> None:
    parsed = _parse_url(
        value,
        field_name="oidc_issuer",
        allowed_schemes={"http", "https"},
    )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("oidc_issuer cannot include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("oidc_issuer cannot include a query or fragment")
    if strict and parsed.scheme != "https":
        raise ValueError("oidc_issuer must use https outside local/test")
    if strict and _is_local_hostname(parsed.hostname or ""):
        raise ValueError("oidc_issuer cannot use a local hostname outside local/test")


class _StandaloneSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="STANDALONE_",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
        validate_default=True,
    )

    environment: Environment = "local"


class Settings(_StandaloneSettings):
    application_id: str = "standalone-example"
    platform_api_base_url: str = "http://localhost:8000"
    database_url: str = (
        "postgresql+psycopg://standalone_app:local-only-standalone-password@"
        "localhost:5433/standalone_app_db"
    )
    oidc_issuer: str = "http://localhost:9000/application/o/standalone-example/"
    oidc_audience: str = "standalone-example"
    oidc_client_id: str = "standalone-example"
    oidc_client_secret: str = "local-only-standalone-oidc-client-secret"
    oidc_redirect_uri: str = "http://localhost:8100/auth/callback"
    oidc_jwks_cache_ttl_seconds: int = 300
    oidc_jwks_stale_ttl_seconds: int = 3600
    session_secret: str = "local-only-standalone-session-signing-secret"
    authorization_cache_stale_ttl_seconds: int = 300
    integration_capabilities: str = "API_CLIENT"

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(
            capability.strip()
            for capability in self.integration_capabilities.split(",")
            if capability.strip()
        )

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        strict = _is_strict_environment(self.environment)
        _validate_application_id(self.application_id)
        _validate_platform_api_base_url(self.platform_api_base_url, strict=strict)
        _validate_oidc_issuer(self.oidc_issuer, strict=strict)
        _validate_database_url(
            self.database_url,
            field_name="database_url",
            strict=strict,
        )
        if strict and _has_placeholder_secret(self.oidc_client_secret):
            raise ValueError("oidc_client_secret cannot use a placeholder outside local/test")
        if strict and _has_placeholder_secret(self.session_secret):
            raise ValueError("session_secret cannot use a placeholder outside local/test")
        if len(self.session_secret) < 32:
            raise ValueError("session_secret must contain at least 32 characters")
        if self.oidc_jwks_cache_ttl_seconds < 1:
            raise ValueError("oidc_jwks_cache_ttl_seconds must be positive")
        if self.oidc_jwks_stale_ttl_seconds < self.oidc_jwks_cache_ttl_seconds:
            raise ValueError(
                "oidc_jwks_stale_ttl_seconds must not be shorter than the fresh cache TTL"
            )
        if self.authorization_cache_stale_ttl_seconds < 1:
            raise ValueError("authorization_cache_stale_ttl_seconds must be positive")
        unknown_capabilities = self.capabilities - {
            "API_CLIENT",
            "DATA_INGEST",
        }
        if unknown_capabilities:
            raise ValueError("integration_capabilities contains an unsupported capability")
        if "API_CLIENT" not in self.capabilities:
            raise ValueError("integration_capabilities must include API_CLIENT")
        return self


class MigrationSettings(_StandaloneSettings):
    migration_database_url: str = (
        "postgresql+psycopg://standalone_app_migrator:"
        "local-only-standalone-migrator-password@localhost:5433/standalone_app_db"
    )

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        _validate_database_url(
            self.migration_database_url,
            field_name="migration_database_url",
            strict=_is_strict_environment(self.environment),
        )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_migration_settings() -> MigrationSettings:
    return MigrationSettings()
