from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal, Self
from urllib.parse import SplitResult, unquote, urlsplit

from pydantic import SecretStr, model_validator
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
        raise ValueError("application_id must contain 3-63 lowercase letters, numbers, or hyphens")


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


def _validate_oidc_issuer(
    value: str,
    *,
    field_name: str = "oidc_issuer",
    strict: bool,
) -> None:
    parsed = _parse_url(
        value,
        field_name=field_name,
        allowed_schemes={"http", "https"},
    )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} cannot include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} cannot include a query or fragment")
    if strict and parsed.scheme != "https":
        raise ValueError(f"{field_name} must use https outside local/test")
    if strict and _is_local_hostname(parsed.hostname or ""):
        raise ValueError(f"{field_name} cannot use a local hostname outside local/test")


def _validate_redirect_uri(value: str, *, field_name: str, strict: bool) -> None:
    parsed = _parse_url(
        value,
        field_name=field_name,
        allowed_schemes={"http", "https"},
    )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} cannot include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} cannot include a query or fragment")
    if strict and parsed.scheme != "https":
        raise ValueError(f"{field_name} must use https outside local/test")
    if strict and _is_local_hostname(parsed.hostname or ""):
        raise ValueError(f"{field_name} cannot use a local hostname outside local/test")


def _validate_rabbitmq_url(value: str, *, strict: bool) -> None:
    parsed = _parse_url(
        value,
        field_name="rabbitmq_url",
        allowed_schemes={"amqp", "amqps"},
    )
    if not parsed.username or not parsed.password:
        raise ValueError("rabbitmq_url must include a username and password")
    if not parsed.path.strip("/"):
        raise ValueError("rabbitmq_url must include an environment vhost")
    if strict and parsed.scheme != "amqps":
        raise ValueError("rabbitmq_url must use amqps outside local/test")
    if strict and _is_local_hostname(parsed.hostname or ""):
        raise ValueError("rabbitmq_url cannot use a local hostname outside local/test")
    if strict and _has_placeholder_secret(unquote(parsed.password)):
        raise ValueError("rabbitmq_url cannot use a placeholder password outside local/test")


class _PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AI_HUB_",
        env_ignore_empty=True,
        extra="ignore",
        hide_input_in_errors=True,
        validate_default=True,
    )

    environment: Environment = "local"


class Settings(_PlatformSettings):
    service_name: str = "ai-hub-platform"
    application_id: str = "ai-hub-platform"
    database_url: str = (
        "postgresql+psycopg://ai_hub_platform:local-only-platform-password@"
        "localhost:5433/platform_db"
    )
    oidc_issuer: str = "http://localhost:9000/application/o/ai-hub/"
    oidc_audience: str = "ai-hub-platform"
    oidc_jwks_cache_ttl_seconds: int = 300
    oidc_jwks_stale_ttl_seconds: int = 3600
    authorization_cache_ttl_seconds: int = 60
    portal_oidc_issuer: str = "http://localhost:9000/application/o/ai-hub-portal/"
    portal_oidc_audience: str = "ai-hub-portal"
    portal_oidc_client_id: str = "ai-hub-portal"
    portal_oidc_client_secret: SecretStr = SecretStr("local-only-portal-oidc-client-secret")
    portal_oidc_redirect_uri: str = "http://platform.localhost:8088/auth/callback"
    portal_session_ttl_seconds: int = 900
    portal_login_ttl_seconds: int = 300
    portal_session_cookie_name: str = "ai_hub_portal_session"
    portal_csrf_cookie_name: str = "ai_hub_portal_csrf"
    authentik_api_url: str = "http://localhost:9000/api/v3"
    authentik_external_url: str = "http://auth.localhost:8088"
    authentik_api_token: SecretStr = SecretStr("local-only-authentik-automation-api-token")
    authentik_provider_template_client_id: str = "ai-hub-platform"
    credential_rotation_overlap_seconds: int = 300
    public_asset_root: str = "/workspace/public-assets"
    public_platform_base_url: str = "http://platform.localhost:8088"
    public_identity_base_url: str = "http://auth.localhost:8088"
    sandbox_application_id: str = "standalone-example"
    sandbox_user_subject: str = "ai-hub-demo-user"
    monitor_token: SecretStr | None = None
    production_targets_path: str = "deploy/operations/production-targets.json"
    operations_rabbitmq_management_url: str | None = None
    operations_rabbitmq_vhost: str = "ai-hub-local"
    operations_rabbitmq_username: str | None = None
    operations_rabbitmq_password: SecretStr | None = None

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        strict = _is_strict_environment(self.environment)
        _validate_application_id(self.application_id)
        _validate_database_url(
            self.database_url,
            field_name="database_url",
            strict=strict,
        )
        _validate_oidc_issuer(self.oidc_issuer, strict=strict)
        _validate_oidc_issuer(
            self.portal_oidc_issuer,
            field_name="portal_oidc_issuer",
            strict=strict,
        )
        _validate_redirect_uri(
            self.portal_oidc_redirect_uri,
            field_name="portal_oidc_redirect_uri",
            strict=strict,
        )
        _validate_redirect_uri(
            self.authentik_api_url,
            field_name="authentik_api_url",
            strict=strict,
        )
        _validate_redirect_uri(
            self.authentik_external_url,
            field_name="authentik_external_url",
            strict=strict,
        )
        if strict and _has_placeholder_secret(self.portal_oidc_client_secret.get_secret_value()):
            raise ValueError(
                "portal_oidc_client_secret cannot use a placeholder outside local/test"
            )
        if strict and _has_placeholder_secret(self.authentik_api_token.get_secret_value()):
            raise ValueError("authentik_api_token cannot use a placeholder outside local/test")
        if not self.portal_oidc_client_id.strip():
            raise ValueError("portal_oidc_client_id cannot be empty")
        if self.oidc_jwks_cache_ttl_seconds < 1:
            raise ValueError("oidc_jwks_cache_ttl_seconds must be positive")
        if self.oidc_jwks_stale_ttl_seconds < self.oidc_jwks_cache_ttl_seconds:
            raise ValueError(
                "oidc_jwks_stale_ttl_seconds must not be shorter than the fresh cache TTL"
            )
        if not 1 <= self.authorization_cache_ttl_seconds <= 300:
            raise ValueError("authorization_cache_ttl_seconds must be between 1 and 300")
        if not 60 <= self.portal_session_ttl_seconds <= 3600:
            raise ValueError("portal_session_ttl_seconds must be between 60 and 3600")
        if not 60 <= self.portal_login_ttl_seconds <= 600:
            raise ValueError("portal_login_ttl_seconds must be between 60 and 600")
        if not self.portal_session_cookie_name.strip():
            raise ValueError("portal_session_cookie_name cannot be empty")
        if not self.portal_csrf_cookie_name.strip():
            raise ValueError("portal_csrf_cookie_name cannot be empty")
        if self.portal_session_cookie_name == self.portal_csrf_cookie_name:
            raise ValueError("portal session and CSRF cookie names must be different")
        if not self.authentik_provider_template_client_id.strip():
            raise ValueError("authentik_provider_template_client_id cannot be empty")
        minimum_overlap = 300 if strict else 1
        if not minimum_overlap <= self.credential_rotation_overlap_seconds <= 3600:
            raise ValueError(
                "credential_rotation_overlap_seconds must be between "
                f"{minimum_overlap} and 3600"
            )
        _validate_redirect_uri(
            self.public_platform_base_url,
            field_name="public_platform_base_url",
            strict=strict,
        )
        _validate_redirect_uri(
            self.public_identity_base_url,
            field_name="public_identity_base_url",
            strict=strict,
        )
        _validate_application_id(self.sandbox_application_id)
        if not self.sandbox_user_subject.strip():
            raise ValueError("sandbox_user_subject cannot be empty")
        if strict and self.monitor_token is not None:
            if _has_placeholder_secret(self.monitor_token.get_secret_value()):
                raise ValueError("monitor_token cannot use a placeholder outside local/test")
        if not self.production_targets_path.strip():
            raise ValueError("production_targets_path cannot be empty")
        operations_values = (
            self.operations_rabbitmq_management_url,
            self.operations_rabbitmq_username,
            self.operations_rabbitmq_password,
        )
        if any(value is not None for value in operations_values) and not all(
            value is not None for value in operations_values
        ):
            raise ValueError(
                "RabbitMQ operations URL, username, and password must be configured together"
            )
        if self.operations_rabbitmq_management_url is not None:
            _validate_redirect_uri(
                self.operations_rabbitmq_management_url,
                field_name="operations_rabbitmq_management_url",
                strict=strict,
            )
            if not self.operations_rabbitmq_vhost.strip():
                raise ValueError("operations_rabbitmq_vhost cannot be empty")
            if strict and self.operations_rabbitmq_password is not None:
                if _has_placeholder_secret(self.operations_rabbitmq_password.get_secret_value()):
                    raise ValueError(
                        "operations_rabbitmq_password cannot use a placeholder outside local/test"
                    )
        return self


class CoreMigrationSettings(_PlatformSettings):
    migration_database_url: str = (
        "postgresql+psycopg://ai_hub_platform_migrator:"
        "local-only-platform-migrator-password@localhost:5433/platform_db"
    )

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        _validate_database_url(
            self.migration_database_url,
            field_name="migration_database_url",
            strict=_is_strict_environment(self.environment),
        )
        return self


class ProjectionMigrationSettings(_PlatformSettings):
    projection_migration_database_url: str = (
        "postgresql+psycopg://ai_hub_projection_migrator:"
        "local-only-projection-migrator-password@localhost:5433/platform_db"
    )

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        _validate_database_url(
            self.projection_migration_database_url,
            field_name="projection_migration_database_url",
            strict=_is_strict_environment(self.environment),
        )
        return self


class ProjectionWorkerSettings(_PlatformSettings):
    projection_database_url: str = (
        "postgresql+psycopg://ai_hub_projection:local-only-projection-password@"
        "localhost:5433/platform_db"
    )
    rabbitmq_url: str = (
        "amqp://platform_projection:local-only-rabbitmq-projection-password@"
        "localhost:5672/ai-hub-local"
    )
    queue_name: str = "ai-hub.platform.projection"
    prefetch_count: int = 20
    max_redeliveries: int = 5
    connection_timeout_seconds: float = 10.0
    processing_delay_seconds: float = 0.0
    acknowledgement_delay_seconds: float = 0.0

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        strict = _is_strict_environment(self.environment)
        _validate_database_url(
            self.projection_database_url,
            field_name="projection_database_url",
            strict=strict,
        )
        _validate_rabbitmq_url(self.rabbitmq_url, strict=strict)
        if self.queue_name != "ai-hub.platform.projection":
            raise ValueError("queue_name must use the registered projection queue")
        if not 1 <= self.prefetch_count <= 500:
            raise ValueError("prefetch_count must be between 1 and 500")
        if not 1 <= self.max_redeliveries <= 20:
            raise ValueError("max_redeliveries must be between 1 and 20")
        if self.connection_timeout_seconds <= 0:
            raise ValueError("connection_timeout_seconds must be positive")
        for value in (
            self.processing_delay_seconds,
            self.acknowledgement_delay_seconds,
        ):
            if not 0 <= value <= 60:
                raise ValueError("worker diagnostic delays must be between 0 and 60 seconds")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_core_migration_settings() -> CoreMigrationSettings:
    return CoreMigrationSettings()


@lru_cache
def get_projection_migration_settings() -> ProjectionMigrationSettings:
    return ProjectionMigrationSettings()


@lru_cache
def get_projection_worker_settings() -> ProjectionWorkerSettings:
    return ProjectionWorkerSettings()
