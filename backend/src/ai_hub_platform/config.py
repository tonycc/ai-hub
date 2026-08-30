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


# Cluster-internal hostnames that may serve plain HTTP even in strict mode.
# Authentik's admin API carries a Bearer token, so HTTP is only acceptable on
# the private overlay network, never on a public address.
# Exact cluster-internal service names plus the Kubernetes internal DNS
# suffix. Anything else must use HTTPS in strict mode, because the admin API
# carries a Bearer token and public HTTP would disclose it.
_CLUSTER_INTERNAL_HOSTS = frozenset(
    {
        "authentik-server",
        "postgres",
        "traefik",
    }
)
_K8S_INTERNAL_SUFFIXES = (".svc.cluster.local", ".cluster.local")


def _is_cluster_internal_hostname(hostname: str) -> bool:
    normalized = hostname.lower()
    if normalized in _CLUSTER_INTERNAL_HOSTS:
        return True
    # k8s fully-qualified internal names: <service>.<namespace>.svc[.cluster.local]
    for suffix in _K8S_INTERNAL_SUFFIXES:
        if normalized.endswith(suffix):
            labels = normalized[: -len(suffix)].split(".")
            # Require at least service + namespace, all non-empty and with a
            # valid DNS label shape; this rejects prefix-spoofed public names
            # like authentik-server.evil.example.
            if len(labels) < 2:
                return False
            return all(
                label
                and len(label) <= 63
                and label[0].isalnum()
                and label[-1].isalnum()
                and all(char.isalnum() or char == "-" for char in label)
                for label in labels
            )
    return False


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


def _validate_internal_api_url(value: str, *, field_name: str, strict: bool) -> None:
    # Cluster-internal admin endpoints may legitimately use plain HTTP on the
    # private overlay network even in production: routing them through the
    # public Traefik address would form a startup dependency cycle (Traefik
    # waits on platform-api readiness, readiness waits on this very call).
    # Public issuer/brand URLs keep the strict HTTPS + non-local rules.
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
        hostname = parsed.hostname or ""
        if not _is_cluster_internal_hostname(hostname):
            raise ValueError(
                f"{field_name} must use https outside local/test unless the host is "
                f"a known cluster-internal service"
            )
    if strict and parsed.hostname and _is_local_hostname(parsed.hostname):
        raise ValueError(f"{field_name} cannot use a local hostname outside local/test")


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
    portal_oidc_logout_redirect_uri: str = "http://platform.localhost:8088/"
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
    # Public entry points of the reference standalone application. Used when
    # the bootstrap reconciliation needs to create the production environment
    # row, so the portal never points at the identity service.
    standalone_portal_url: str = "http://app.localhost:8088/"
    standalone_api_base_url: str = "http://app.localhost:8088"
    standalone_health_url: str = "http://app.localhost:8088/health"
    standalone_oidc_redirect_uri: str = "http://app.localhost:8088/auth/callback"
    monitor_token: SecretStr | None = None
    production_targets_path: str = "deploy/operations/production-targets.json"
    # Portal ingest ops actions read/write platform_raw and pull export APIs.
    raw_database_url: str = (
        "postgresql+psycopg://ai_hub_raw:local-only-raw-password@"
        "localhost:5433/platform_db"
    )
    oidc_client_id: str = "ai-hub-platform"
    oidc_client_secret: SecretStr = SecretStr("local-only-oidc-client-secret")
    data_ingest_push_enabled: bool = False
    ingest_pull_contract_enforcement_enabled: bool = False

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
        _validate_internal_api_url(
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
        _validate_redirect_uri(
            self.standalone_portal_url,
            field_name="standalone_portal_url",
            strict=strict,
        )
        _validate_redirect_uri(
            self.standalone_api_base_url,
            field_name="standalone_api_base_url",
            strict=strict,
        )
        _validate_redirect_uri(
            self.standalone_health_url,
            field_name="standalone_health_url",
            strict=strict,
        )
        _validate_redirect_uri(
            self.standalone_oidc_redirect_uri,
            field_name="standalone_oidc_redirect_uri",
            strict=strict,
        )
        if not self.sandbox_user_subject.strip():
            raise ValueError("sandbox_user_subject cannot be empty")
        if strict and self.monitor_token is not None:
            if _has_placeholder_secret(self.monitor_token.get_secret_value()):
                raise ValueError("monitor_token cannot use a placeholder outside local/test")
        if not self.production_targets_path.strip():
            raise ValueError("production_targets_path cannot be empty")
        _validate_database_url(
            self.raw_database_url,
            field_name="raw_database_url",
            strict=strict,
        )
        if not self.oidc_client_id.strip():
            raise ValueError("oidc_client_id cannot be empty")
        if strict and _has_placeholder_secret(self.oidc_client_secret.get_secret_value()):
            raise ValueError("oidc_client_secret cannot use a placeholder outside local/test")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_core_migration_settings() -> CoreMigrationSettings:
    return CoreMigrationSettings()


class RawMigrationSettings(_PlatformSettings):
    raw_migration_database_url: str = (
        "postgresql+psycopg://ai_hub_raw_migrator:"
        "local-only-raw-migrator-password@localhost:5433/platform_db"
    )

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        _validate_database_url(
            self.raw_migration_database_url,
            field_name="raw_migration_database_url",
            strict=_is_strict_environment(self.environment),
        )
        return self


class RawWorkerSettings(_PlatformSettings):
    raw_database_url: str = (
        "postgresql+psycopg://ai_hub_raw:local-only-raw-password@"
        "localhost:5433/platform_db"
    )
    ingest_sources_path: str = "deploy/operations/ingest-sources.json"
    tick_interval_seconds: float = 1.0
    http_timeout_seconds: float = 30.0
    max_concurrent_sources: int = 4
    max_concurrent_per_application: int = 2
    oidc_issuer: str = "http://localhost:9000/application/o/ai-hub/"
    oidc_client_id: str = "ai-hub-platform"
    oidc_client_secret: SecretStr = SecretStr("local-only-oidc-client-secret")
    # Used only by ai-hub-ingest-seed to write platform_core; compose points this at
    # the platform migrator role. One-shot sync/reconcile CLIs use raw_database_url.
    seed_database_url: str | None = None
    data_ingest_push_enabled: bool = False
    ingest_pull_contract_enforcement_enabled: bool = False

    @model_validator(mode="after")
    def validate_configuration(self) -> Self:
        strict = _is_strict_environment(self.environment)
        _validate_database_url(
            self.raw_database_url,
            field_name="raw_database_url",
            strict=strict,
        )
        if not self.ingest_sources_path.strip():
            raise ValueError("ingest_sources_path cannot be empty")
        if self.tick_interval_seconds <= 0:
            raise ValueError("tick_interval_seconds must be positive")
        if self.http_timeout_seconds <= 0:
            raise ValueError("http_timeout_seconds must be positive")
        if not 1 <= self.max_concurrent_sources <= 64:
            raise ValueError("max_concurrent_sources must be between 1 and 64")
        if not 1 <= self.max_concurrent_per_application <= 16:
            raise ValueError(
                "max_concurrent_per_application must be between 1 and 16"
            )
        if self.max_concurrent_per_application > self.max_concurrent_sources:
            raise ValueError(
                "max_concurrent_per_application cannot exceed max_concurrent_sources"
            )
        _validate_oidc_issuer(self.oidc_issuer, strict=strict)
        if not self.oidc_client_id.strip():
            raise ValueError("oidc_client_id cannot be empty")
        if strict and _has_placeholder_secret(self.oidc_client_secret.get_secret_value()):
            raise ValueError(
                "oidc_client_secret cannot use a placeholder outside local/test"
            )
        if self.seed_database_url is not None:
            _validate_database_url(
                self.seed_database_url,
                field_name="seed_database_url",
                strict=strict,
            )
        return self


@lru_cache
def get_raw_migration_settings() -> RawMigrationSettings:
    return RawMigrationSettings()


@lru_cache
def get_raw_worker_settings() -> RawWorkerSettings:
    return RawWorkerSettings()
