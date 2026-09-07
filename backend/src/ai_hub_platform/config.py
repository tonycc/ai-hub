from __future__ import annotations

import re
from functools import lru_cache
from ipaddress import IPv4Address, IPv4Network
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
_DEFAULT_PORTAL_LOGOUT_REDIRECT_URI = "http://platform.localhost:8088/"
_PRIVATE_IPV4_NETWORKS = tuple(
    IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


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


def _normalize_origin(value: str, *, field_name: str, strict: bool) -> str:
    if value != value.lower():
        raise ValueError(f"{field_name} must use lowercase")
    parsed = _parse_url(value, field_name=field_name, allowed_schemes={"http", "https"})
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} cannot include credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must be an Origin without path, query, or fragment")
    if strict and parsed.scheme != "https":
        raise ValueError(f"{field_name} must use https outside local/test")
    hostname = parsed.hostname or ""
    if ":" in hostname:
        raise ValueError(f"{field_name} does not support IPv6")
    try:
        address = IPv4Address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if strict and len(labels) < 2:
            raise ValueError(f"{field_name} must use a complete DNS name") from None
        if len(hostname) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in labels
        ):
            raise ValueError(f"{field_name} contains an invalid DNS name") from None
    else:
        if not any(address in network for network in _PRIVATE_IPV4_NETWORKS):
            raise ValueError(f"{field_name} IPv4 address must be RFC1918 private")
    normalized_port = parsed.port
    if (parsed.scheme, normalized_port) in {("http", 80), ("https", 443)}:
        normalized_port = None
    port = f":{normalized_port}" if normalized_port is not None else ""
    return f"{parsed.scheme}://{hostname}{port}"


def _parse_origin_csv(value: str, *, field_name: str, strict: bool) -> tuple[str, ...]:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} cannot contain control characters")
    items = tuple(item.strip() for item in value.split(","))
    if not items or any(not item for item in items):
        raise ValueError(f"{field_name} cannot contain empty entries")
    origins = tuple(
        _normalize_origin(item, field_name=f"{field_name}[{index}]", strict=strict)
        for index, item in enumerate(items)
    )
    if len(set(origins)) != len(origins):
        raise ValueError(f"{field_name} cannot contain duplicate Origins")
    return origins


def _url_origin(value: str, *, field_name: str, strict: bool) -> str:
    parsed = _parse_url(value, field_name=field_name, allowed_schemes={"http", "https"})
    normalized_port = parsed.port
    if (parsed.scheme, normalized_port) in {("http", 80), ("https", 443)}:
        normalized_port = None
    port = f":{normalized_port}" if normalized_port is not None else ""
    return _normalize_origin(
        f"{parsed.scheme}://{parsed.hostname}{port}",
        field_name=field_name,
        strict=strict,
    )


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
    portal_oidc_logout_redirect_uri: str = _DEFAULT_PORTAL_LOGOUT_REDIRECT_URI
    platform_origins: str = ""
    platform_default_origin: str = ""
    portal_oidc_redirect_uris: str = ""
    portal_oidc_logout_redirect_uris: str = ""
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
    # The neutral reference app is a local/CI conformance fixture. Production
    # overlays disable it explicitly so it is not exposed as a business app.
    reference_application_enabled: bool = True
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
        self._portal_origin_configuration(strict=strict)
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
        if self.reference_application_enabled:
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

    def portal_allowed_origins(self) -> tuple[str, ...]:
        return self._portal_origin_configuration(
            strict=_is_strict_environment(self.environment)
        )[0]

    def portal_default_origin_value(self) -> str:
        return self._portal_origin_configuration(
            strict=_is_strict_environment(self.environment)
        )[1]

    def portal_redirect_uri_for_origin(self, origin: str) -> str:
        origins, _, redirects, _ = self._portal_origin_configuration(
            strict=_is_strict_environment(self.environment)
        )
        try:
            return redirects[origins.index(origin)]
        except ValueError as error:
            raise ValueError("portal request Origin is not allowed") from error

    def portal_logout_uri_for_origin(self, origin: str) -> str:
        origins, _, _, logout_uris = self._portal_origin_configuration(
            strict=_is_strict_environment(self.environment)
        )
        try:
            return logout_uris[origins.index(origin)]
        except ValueError as error:
            raise ValueError("portal request Origin is not allowed") from error

    def _portal_origin_configuration(
        self,
        *,
        strict: bool,
    ) -> tuple[tuple[str, ...], str, tuple[str, ...], tuple[str, ...]]:
        if not self.platform_origins.strip():
            origin = _url_origin(
                self.portal_oidc_redirect_uri,
                field_name="portal_oidc_redirect_uri",
                strict=strict,
            )
            logout_uri = self._legacy_logout_uri(origin=origin, strict=strict)
            logout_origin = _url_origin(
                logout_uri, field_name="portal_oidc_logout_redirect_uri", strict=strict
            )
            if logout_origin != origin:
                raise ValueError("portal OIDC redirect and logout URI Origins must match")
            return (
                (origin,),
                origin,
                (self.portal_oidc_redirect_uri,),
                (logout_uri,),
            )

        origins = _parse_origin_csv(
            self.platform_origins,
            field_name="platform_origins",
            strict=strict,
        )
        default_origin = (
            _normalize_origin(
                self.platform_default_origin,
                field_name="platform_default_origin",
                strict=strict,
            )
            if self.platform_default_origin.strip()
            else origins[0]
        )
        if default_origin not in origins:
            raise ValueError("platform_default_origin must be listed in platform_origins")
        redirects = self._portal_uri_list(
            self.portal_oidc_redirect_uris,
            origins=origins,
            field_name="portal_oidc_redirect_uris",
            default_path="/auth/callback",
            strict=strict,
        )
        logout_uris = self._portal_uri_list(
            self.portal_oidc_logout_redirect_uris,
            origins=origins,
            field_name="portal_oidc_logout_redirect_uris",
            default_path="/",
            strict=strict,
        )
        legacy_redirect_origin = _url_origin(
            self.portal_oidc_redirect_uri,
            field_name="portal_oidc_redirect_uri",
            strict=strict,
        )
        legacy_logout_uri = self._legacy_logout_uri(origin=origins[0], strict=strict)
        legacy_logout_origin = _url_origin(
            legacy_logout_uri, field_name="portal_oidc_logout_redirect_uri", strict=strict
        )
        if (
            legacy_redirect_origin not in origins
            or redirects[origins.index(legacy_redirect_origin)] != self.portal_oidc_redirect_uri
            or legacy_logout_origin not in origins
            or logout_uris[origins.index(legacy_logout_origin)]
            != legacy_logout_uri
        ):
            raise ValueError("multi-Origin portal settings conflict with legacy portal OIDC URIs")
        return origins, default_origin, redirects, logout_uris

    def _legacy_logout_uri(self, *, origin: str, strict: bool) -> str:
        if strict and self.portal_oidc_logout_redirect_uri == _DEFAULT_PORTAL_LOGOUT_REDIRECT_URI:
            return f"{origin}/"
        return self.portal_oidc_logout_redirect_uri

    @staticmethod
    def _portal_uri_list(
        value: str,
        *,
        origins: tuple[str, ...],
        field_name: str,
        default_path: str,
        strict: bool,
    ) -> tuple[str, ...]:
        uris = (
            tuple(f"{origin}{default_path}" for origin in origins)
            if not value.strip()
            else tuple(item.strip() for item in value.split(","))
        )
        if len(uris) != len(origins) or any(not uri for uri in uris):
            raise ValueError(f"{field_name} must have exactly one URI per platform Origin")
        for index, (origin, uri) in enumerate(zip(origins, uris, strict=True)):
            _validate_redirect_uri(uri, field_name=f"{field_name}[{index}]", strict=strict)
            if _url_origin(uri, field_name=f"{field_name}[{index}]", strict=strict) != origin:
                raise ValueError(f"{field_name}[{index}] must use the matching platform Origin")
        if len(set(uris)) != len(uris):
            raise ValueError(f"{field_name} cannot contain duplicate URIs")
        return uris


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
