from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: str) -> dict[str, Any]:
    payload = yaml.safe_load((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def test_base_access_profile_includes_authentik_and_traefik_without_docker_socket() -> None:
    compose = load_yaml("deploy/compose.yaml")
    services = compose["services"]
    expected_profiles = ["base-access"]

    for service_name in ("authentik-server", "authentik-worker", "traefik"):
        assert services[service_name]["profiles"] == expected_profiles
    serialized = (PROJECT_ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in serialized
    assert "./authentik/ai-hub-blueprint.yaml:/blueprints/" in serialized
    assert "rabbitmq" not in services
    assert "standard-events" not in serialized


def test_authentik_file_storage_is_initialized_as_persistent_real_directories() -> None:
    compose = load_yaml("deploy/compose.yaml")
    services = compose["services"]

    initializer = services["authentik-storage-init"]
    assert initializer["profiles"] == ["base-access"]
    assert initializer["user"] == "0:0"
    assert "media.is_symlink()" in initializer["command"][0]
    assert "media.mkdir" in initializer["command"][0]
    assert "os.chown(data, 1000, 1000)" in initializer["command"][0]
    for service_name in ("authentik-storage-init", "authentik-server", "authentik-worker"):
        data_mount = services[service_name]["volumes"][0]
        assert data_mount == {
            "type": "volume",
            "source": "authentik-data",
            "target": "/data",
            "volume": {"nocopy": True},
        }
    assert services["authentik-server"]["depends_on"]["authentik-storage-init"] == {
        "condition": "service_completed_successfully"
    }


def test_traefik_is_only_ingress_and_routes_every_public_host() -> None:
    static = load_yaml("deploy/traefik/traefik.yaml")
    dynamic = load_yaml("deploy/traefik/dynamic.yaml")

    assert static["providers"] == {"file": {"filename": "/etc/traefik/dynamic.yaml", "watch": True}}
    assert static["api"]["dashboard"] is False
    assert set(dynamic["http"]["routers"]) == {
        "authentik",
        "platform-api",
        "platform-portal",
        "standalone-app",
    }
    assert (
        dynamic["http"]["services"]["authentik"]["loadBalancer"]["healthCheck"]["path"]
        == "/-/health/ready/"
    )


def test_authentik_blueprint_has_strict_oidc_and_minimal_scopes() -> None:
    blueprint = (PROJECT_ROOT / "deploy/authentik/ai-hub-blueprint.yaml").read_text(
        encoding="utf-8"
    )

    assert "grant_types:" in blueprint
    assert "authorization_code" in blueprint
    assert "client_credentials" in blueprint
    assert "matching_mode: strict" in blueprint
    assert "issuer_mode: per_provider" in blueprint
    assert "access_token_validity: minutes=5" in blueprint
    for scope in (
        "platform.me.read",
        "platform.application.read",
        "platform.authorization.decide",
        "platform.notification.request",
        "platform.application.health.write",
        "ai_hub.ingest.export",
        "ai_hub.ingest.push",
        "platform.data.read",
    ):
        assert f"scope_name: {scope}" in blueprint


def test_ingest_operator_uses_independent_password_secret() -> None:
    blueprint = (PROJECT_ROOT / "deploy/authentik/ai-hub-blueprint.yaml").read_text(
        encoding="utf-8"
    )
    compose = (PROJECT_ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    generator = (
        PROJECT_ROOT / "scripts/deploy/generate-runtime-env.sh"
    ).read_text(encoding="utf-8")
    start = blueprint.index("username: ai-hub-platform-ingest-operator")
    snippet = blueprint[start : start + 400]
    assert "password: !Env AI_HUB_INGEST_OPERATOR_PASSWORD" in snippet
    assert "AI_HUB_UAT_USER_PASSWORD" not in snippet
    assert (
        "AI_HUB_INGEST_OPERATOR_PASSWORD: ${AI_HUB_INGEST_OPERATOR_PASSWORD:?"
        in compose
    )
    assert "AI_HUB_INGEST_OPERATOR_PASSWORD=$(gen_secret 32)" in generator
    uat = next(
        line.split("=", 1)[1]
        for line in example.splitlines()
        if line.startswith("AI_HUB_UAT_USER_PASSWORD=")
    )
    operator = next(
        line.split("=", 1)[1]
        for line in example.splitlines()
        if line.startswith("AI_HUB_INGEST_OPERATOR_PASSWORD=")
    )
    assert uat != operator


def test_standalone_image_build_does_not_copy_platform_source() -> None:
    dockerfile = (PROJECT_ROOT / "examples/standalone-app/Dockerfile").read_text(encoding="utf-8")

    assert "COPY backend/src" not in dockerfile
    assert "COPY sdk/python/src" in dockerfile


def test_platform_api_readiness_probe_allows_bootstrap_reconciliation_window() -> None:
    compose = load_yaml("deploy/compose.yaml")
    healthcheck = compose["services"]["platform-api"]["healthcheck"]
    test_command = " ".join(healthcheck["test"])

    assert "/health/ready" in test_command
    assert "/health/live" not in test_command
    # Bootstrap retries sleep 5/10/20/30s before the dedicated provider may
    # appear; a short start_period fails compose --wait in M1 before reconcile.
    assert healthcheck["start_period"] == "90s"
    assert int(str(healthcheck["retries"])) >= 24


def test_standalone_app_uses_api_client_and_data_ingest_only() -> None:
    compose = load_yaml("deploy/compose.yaml")
    services = compose["services"]
    application = services["standalone-app"]

    assert application["profiles"] == ["base-access"]
    assert application["environment"]["STANDALONE_INTEGRATION_CAPABILITIES"] == (
        "API_CLIENT,DATA_INGEST"
    )
    assert "standalone-outbox-publisher" not in services
    assert "standalone-app-events" not in services
    assert "standalone-consumer-db-bootstrap" not in services
    assert "standalone-publisher-db-bootstrap" not in services


def test_platform_operations_does_not_configure_a_rabbitmq_observer() -> None:
    compose = load_yaml("deploy/compose.yaml")
    environment = compose["services"]["platform-api"]["environment"]
    serialized_env = "\n".join(f"{key}={value}" for key, value in environment.items())
    local_environment = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "RABBITMQ" not in serialized_env
    assert "RABBITMQ" not in local_environment


def test_m1_revokes_the_authoritative_application_credential() -> None:
    runtime_gate = (PROJECT_ROOT / "scripts/ci/m1-runtime.sh").read_text(encoding="utf-8")

    assert "UPDATE platform_core.application_credential SET status = 'REVOKED'" in runtime_gate
    assert "UPDATE platform_core.application SET service_subject = NULL" not in runtime_gate
