from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = PROJECT_ROOT / "contracts/api/platform-api.openapi.yaml"


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return cast(dict[str, Any], document)


def resolve_local_json_pointer(document: dict[str, Any], reference: str) -> Any:
    assert reference.startswith("#/")
    current: Any = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        assert isinstance(current, dict)
        current_mapping = cast(dict[str, Any], current)
        assert part in current_mapping
        current = current_mapping[part]
    return current


def test_openapi_contract_has_unique_operations_and_resolvable_local_refs() -> None:
    contract = load_yaml_mapping(OPENAPI_PATH)

    assert contract["openapi"] == "3.1.0"
    assert contract["info"]["version"] == "0.4.0"
    assert contract["paths"]

    operation_ids: list[str] = []
    pending: list[Any] = [contract]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            current_mapping = cast(dict[str, Any], current)
            reference = current_mapping.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/"):
                resolve_local_json_pointer(contract, reference)
            operation_id = current_mapping.get("operationId")
            if isinstance(operation_id, str):
                operation_ids.append(operation_id)
            pending.extend(current_mapping.values())
        elif isinstance(current, list):
            pending.extend(cast(list[Any], current))

    assert operation_ids
    assert len(operation_ids) == len(set(operation_ids))
    push_record = contract["components"]["schemas"]["PushRecord"]["properties"]["version"]
    assert push_record["minimum"] == 1
    bootstrap = contract["components"]["schemas"]["AdminBootstrapClaim"]
    assert "initial_admin_user_id" in bootstrap["required"]
    assert "owner_user_id" not in bootstrap["properties"]
    assert "business_user" in contract["components"]["schemas"]["CurrentUser"]["required"]
    assert "business_user" in contract["components"]["schemas"]["DirectoryUser"]["required"]


def test_m1_openapi_covers_every_public_identity_and_api_operation() -> None:
    contract = load_yaml_mapping(OPENAPI_PATH)
    expected_paths = {
        "/health/live",
        "/health/ready",
        "/platform-api/v1/me",
        "/platform-api/v1/me/permissions",
        "/platform-api/v1/authorization/decisions",
        "/platform-api/v1/applications/{application_id}",
        "/platform-api/v1/applications/{application_id}/environments/{environment}/admin-bootstrap",
        "/platform-api/v1/applications/{application_id}/environments/{environment}/health-check",
        "/platform-api/v1/directory/users",
        "/platform-api/v1/notifications",
        "/platform-api/v1/notifications/{notification_id}",
        "/platform-api/v1/data/objects",
        "/platform-api/v1/data/objects/{source_application_id}/{object_type}/{object_id}",
        "/platform-api/v1/data/objects/{source_application_id}/{object_type}/{object_id}/history",
        "/platform-api/v1/ingest/push/capabilities",
        "/platform-api/v1/ingest/push/generations",
        "/platform-api/v1/ingest/push/generations/{generation_id}",
        "/platform-api/v1/ingest/push/generations/{generation_id}/heartbeat",
        "/platform-api/v1/ingest/push/generations/{generation_id}/batches",
        "/platform-api/v1/ingest/push/generations/{generation_id}/complete",
        "/platform-api/v1/ingest/push/generations/{generation_id}/abort",
    }
    assert set(contract["paths"]) == expected_paths
    security_scheme = contract["components"]["securitySchemes"]["oidcAuth"]
    assert security_scheme["type"] == "openIdConnect"
    assert security_scheme["openIdConnectUrl"].endswith(
        "/application/o/ai-hub/.well-known/openid-configuration"
    )

    expected_scopes = {
        "/platform-api/v1/me": ["ai_hub.identity", "platform.me.read"],
        "/platform-api/v1/me/permissions": [
            "ai_hub.identity",
            "platform.me.read",
        ],
        "/platform-api/v1/authorization/decisions": [
            "ai_hub.identity",
            "platform.authorization.decide",
        ],
        "/platform-api/v1/applications/{application_id}": [
            "ai_hub.identity",
            "platform.application.read",
        ],
        (
            "/platform-api/v1/applications/{application_id}"
            "/environments/{environment}/admin-bootstrap"
        ): [
            "ai_hub.identity",
            "platform.application.bootstrap",
        ],
        "/platform-api/v1/applications/{application_id}/environments/{environment}/health-check": [
            "ai_hub.identity",
            "platform.application.health.write",
        ],
        "/platform-api/v1/directory/users": [
            "ai_hub.identity",
            "platform.directory.read",
        ],
        "/platform-api/v1/notifications": [
            "ai_hub.identity",
            "platform.notification.request",
        ],
        "/platform-api/v1/notifications/{notification_id}": [
            "ai_hub.identity",
            "platform.notification.request",
        ],
        "/platform-api/v1/data/objects": [
            "ai_hub.identity",
            "platform.data.read",
        ],
        "/platform-api/v1/data/objects/{source_application_id}/{object_type}/{object_id}": [
            "ai_hub.identity",
            "platform.data.read",
        ],
        "/platform-api/v1/data/objects/{source_application_id}/{object_type}/{object_id}/history": [
            "ai_hub.identity",
            "platform.data.read",
        ],
        "/platform-api/v1/ingest/push/capabilities": [
            "ai_hub.identity",
            "ai_hub.ingest.push",
        ],
        "/platform-api/v1/ingest/push/generations": [
            "ai_hub.identity",
            "ai_hub.ingest.push",
        ],
        "/platform-api/v1/ingest/push/generations/{generation_id}": [
            "ai_hub.identity",
            "ai_hub.ingest.push",
        ],
        "/platform-api/v1/ingest/push/generations/{generation_id}/heartbeat": [
            "ai_hub.identity",
            "ai_hub.ingest.push",
        ],
        "/platform-api/v1/ingest/push/generations/{generation_id}/batches": [
            "ai_hub.identity",
            "ai_hub.ingest.push",
        ],
        "/platform-api/v1/ingest/push/generations/{generation_id}/complete": [
            "ai_hub.identity",
            "ai_hub.ingest.push",
        ],
        "/platform-api/v1/ingest/push/generations/{generation_id}/abort": [
            "ai_hub.identity",
            "ai_hub.ingest.push",
        ],
    }

    for path, path_item in contract["paths"].items():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            operation_mapping = cast(dict[str, Any], operation)
            if path.startswith("/platform-api/"):
                assert operation_mapping.get("security", contract.get("security")) == [
                    {"oidcAuth": expected_scopes[path]}
                ]
                responses = operation_mapping["responses"]
                assert isinstance(responses, dict)
                response_codes = set(cast(dict[str, Any], responses))
                assert "401" in response_codes
                assert "403" in response_codes


def test_push_openapi_lists_runtime_error_statuses() -> None:
    contract = load_yaml_mapping(OPENAPI_PATH)
    paths = cast(dict[str, Any], contract["paths"])
    create = paths["/platform-api/v1/ingest/push/generations"]["post"]["responses"]
    assert {"400", "404", "409", "422", "503"} <= set(create)
    heartbeat = paths[
        "/platform-api/v1/ingest/push/generations/{generation_id}/heartbeat"
    ]["post"]["responses"]
    assert {"400", "404", "409", "422"} <= set(heartbeat)
    batch = paths["/platform-api/v1/ingest/push/generations/{generation_id}/batches"][
        "post"
    ]["responses"]
    assert {"400", "404", "409", "422"} <= set(batch)
    complete = paths[
        "/platform-api/v1/ingest/push/generations/{generation_id}/complete"
    ]["post"]["responses"]
    assert {"400", "404", "409", "422"} <= set(complete)
    abort = paths["/platform-api/v1/ingest/push/generations/{generation_id}/abort"][
        "post"
    ]["responses"]
    assert {"400", "404", "409", "422"} <= set(abort)
    records = contract["components"]["schemas"]["SubmitPushBatchRequest"]["properties"][
        "records"
    ]
    assert records["maxItems"] == 50000
    assert "UnprocessableEntity" in contract["components"]["responses"]
    generation = contract["components"]["schemas"]["PushGeneration"]
    assert generation["properties"]["purpose"]["enum"] == [
        "production",
        "certification",
    ]
    assert "purpose" in generation["required"]


def test_retired_event_contracts_are_not_live() -> None:
    live_events = PROJECT_ROOT / "contracts/events"
    assert not live_events.exists()
