from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_yaml(path: str) -> dict[str, Any]:
    payload = yaml.safe_load((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def test_both_profiles_include_authentik_and_traefik_without_docker_socket() -> None:
    compose = load_yaml("deploy/compose.yaml")
    services = compose["services"]
    expected_profiles = ["base-access", "standard-events"]

    for service_name in ("authentik-server", "authentik-worker", "traefik"):
        assert services[service_name]["profiles"] == expected_profiles
    serialized = (PROJECT_ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in serialized
    assert "./authentik/ai-hub-blueprint.yaml:/blueprints/" in serialized


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
    ):
        assert f"scope_name: {scope}" in blueprint


def test_standalone_image_build_does_not_copy_platform_source() -> None:
    dockerfile = (PROJECT_ROOT / "examples/standalone-app/Dockerfile").read_text(encoding="utf-8")

    assert "COPY backend/src" not in dockerfile
    assert "COPY sdk/python/src" in dockerfile


def test_event_profile_uses_a_dedicated_outbox_relay_database_role() -> None:
    compose = load_yaml("deploy/compose.yaml")
    publisher = compose["services"]["standalone-outbox-publisher"]
    application = compose["services"]["standalone-app"]
    event_application = compose["services"]["standalone-app-events"]

    assert "STANDALONE_PUBLISHER_DATABASE_URL" in publisher["environment"]
    assert (
        "standalone_outbox_publisher"
        in publisher["environment"]["STANDALONE_PUBLISHER_DATABASE_URL"]
    )
    assert "STANDALONE_DATABASE_URL" in application["environment"]
    assert "standalone_app:" in application["environment"]["STANDALONE_DATABASE_URL"]
    assert application["profiles"] == ["base-access"]
    assert event_application["profiles"] == ["standard-events"]
    assert application["environment"]["STANDALONE_INTEGRATION_CAPABILITIES"] == "API_CLIENT"
    assert event_application["environment"]["STANDALONE_INTEGRATION_CAPABILITIES"] == (
        "API_CLIENT,EVENT_PUBLISHER,EVENT_CONSUMER,PROJECTION_SOURCE"
    )


def test_event_database_role_bootstraps_are_serialized() -> None:
    compose = load_yaml("deploy/compose.yaml")
    services = compose["services"]

    assert services["standalone-consumer-db-bootstrap"]["depends_on"][
        "standalone-publisher-db-bootstrap"
    ] == {"condition": "service_completed_successfully"}


def test_platform_operations_uses_the_read_only_rabbitmq_observer() -> None:
    compose = load_yaml("deploy/compose.yaml")
    environment = compose["services"]["platform-api"]["environment"]

    assert environment["AI_HUB_OPERATIONS_RABBITMQ_MANAGEMENT_URL"] == (
        "${AI_HUB_OPERATIONS_RABBITMQ_MANAGEMENT_URL:-}"
    )
    assert environment["AI_HUB_OPERATIONS_RABBITMQ_USERNAME"] == (
        "${RABBITMQ_OBSERVER_USER:-}"
    )
    assert environment["AI_HUB_OPERATIONS_RABBITMQ_PASSWORD"] == (
        "${RABBITMQ_OBSERVER_PASSWORD:-}"
    )

    local_environment = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AI_HUB_OPERATIONS_RABBITMQ_MANAGEMENT_URL=http://rabbitmq:15672" in (
        local_environment
    )
    assert "RABBITMQ_OBSERVER_USER=platform_observer" in local_environment
    assert "RABBITMQ_OBSERVER_PASSWORD=" in local_environment
