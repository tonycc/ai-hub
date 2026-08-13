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
    oidc_issuer: str = "http://localhost:9000/application/o/ai-hub/"
    oidc_audience: str = "ai-hub-platform"
    oidc_client_id: str = "ai-hub-platform"
    oidc_client_secret: str = "local-only-oidc-client-secret"
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
            "EVENT_PUBLISHER",
            "EVENT_CONSUMER",
            "PROJECTION_SOURCE",
            "PROJECTION_READER",
        }
        if unknown_capabilities:
            raise ValueError("integration_capabilities contains an unsupported capability")
        if "API_CLIENT" not in self.capabilities:
            raise ValueError("integration_capabilities must include API_CLIENT")
        if (
            "PROJECTION_SOURCE" in self.capabilities
            and "EVENT_PUBLISHER" not in self.capabilities
        ):
            raise ValueError("PROJECTION_SOURCE requires EVENT_PUBLISHER")
        return self


class EventPublisherSettings(_StandaloneSettings):
    application_id: str = "standalone-example"
    publisher_database_url: str = (
        "postgresql+psycopg://standalone_outbox_publisher:"
        "local-only-standalone-publisher-password@"
        "localhost:5433/standalone_app_db"
    )
    rabbitmq_url: str = (
        "amqp://standalone_publisher:local-only-rabbitmq-publisher-password@"
        "localhost:5672/ai-hub-local"
    )
    exchange_name: str = "ai-hub.events"
    batch_size: int = 50
    max_attempts: int = 8
    retry_base_seconds: int = 1
    retry_max_seconds: int = 60
    lease_seconds: int = 30
    publish_timeout_seconds: float = 10.0
    connection_timeout_seconds: float = 10.0
    poll_interval_seconds: float = 0.5

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        strict = _is_strict_environment(self.environment)
        _validate_application_id(self.application_id)
        _validate_database_url(
            self.publisher_database_url,
            field_name="publisher_database_url",
            strict=strict,
        )
        _validate_rabbitmq_url(self.rabbitmq_url, strict=strict)
        if self.exchange_name != "ai-hub.events":
            raise ValueError("exchange_name must use the registered exchange")
        if not 1 <= self.batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        if not 1 <= self.max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        if not 1 <= self.retry_base_seconds <= self.retry_max_seconds <= 3600:
            raise ValueError("retry timing is invalid")
        if self.lease_seconds < 10:
            raise ValueError("lease_seconds must be at least 10")
        for value in (
            self.publish_timeout_seconds,
            self.connection_timeout_seconds,
            self.poll_interval_seconds,
        ):
            if value <= 0:
                raise ValueError("publisher timeout values must be positive")
        return self


class EventConsumerSettings(_StandaloneSettings):
    consumer_id: str = "standalone-reference-consumer"
    consumer_database_url: str = (
        "postgresql+psycopg://standalone_event_consumer:"
        "local-only-standalone-consumer-password@"
        "localhost:5433/standalone_app_db"
    )
    rabbitmq_url: str = (
        "amqp://standalone_consumer:local-only-rabbitmq-consumer-password@"
        "localhost:5672/ai-hub-local"
    )
    queue_name: str = "ai-hub.standalone.reference-consumer"
    prefetch_count: int = 20
    max_redeliveries: int = 5
    connection_timeout_seconds: float = 10.0

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        strict = _is_strict_environment(self.environment)
        _validate_database_url(
            self.consumer_database_url,
            field_name="consumer_database_url",
            strict=strict,
        )
        _validate_rabbitmq_url(self.rabbitmq_url, strict=strict)
        if not self.consumer_id.strip():
            raise ValueError("consumer_id cannot be empty")
        if self.queue_name != "ai-hub.standalone.reference-consumer":
            raise ValueError("queue_name must use the registered reference consumer queue")
        if not 1 <= self.prefetch_count <= 500:
            raise ValueError("prefetch_count must be between 1 and 500")
        if not 1 <= self.max_redeliveries <= 20:
            raise ValueError("max_redeliveries must be between 1 and 20")
        if self.connection_timeout_seconds <= 0:
            raise ValueError("connection_timeout_seconds must be positive")
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


@lru_cache
def get_event_publisher_settings() -> EventPublisherSettings:
    return EventPublisherSettings()


@lru_cache
def get_event_consumer_settings() -> EventConsumerSettings:
    return EventConsumerSettings()
