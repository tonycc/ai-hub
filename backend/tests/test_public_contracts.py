from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml
from ai_hub_sdk import CloudEvent
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = PROJECT_ROOT / "contracts/api/platform-api.openapi.yaml"
ASYNCAPI_PATH = PROJECT_ROOT / "contracts/events/ai-hub.asyncapi.yaml"
CLOUD_EVENT_SCHEMA_PATH = PROJECT_ROOT / "contracts/events/cloud-event.schema.json"


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
    assert contract["info"]["version"] == "0.2.0"
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


def test_m1_openapi_covers_every_public_identity_and_api_operation() -> None:
    contract = load_yaml_mapping(OPENAPI_PATH)
    expected_paths = {
        "/health/live",
        "/health/ready",
        "/platform-api/v1/me",
        "/platform-api/v1/me/permissions",
        "/platform-api/v1/authorization/decisions",
        "/platform-api/v1/applications/{application_id}",
        "/platform-api/v1/applications/{application_id}/environments/{environment}/health-check",
        "/platform-api/v1/notifications",
        "/platform-api/v1/notifications/{notification_id}",
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
        "/platform-api/v1/applications/{application_id}/environments/{environment}/health-check": [
            "ai_hub.identity",
            "platform.application.health.write",
        ],
        "/platform-api/v1/notifications": [
            "ai_hub.identity",
            "platform.notification.request",
        ],
        "/platform-api/v1/notifications/{notification_id}": [
            "ai_hub.identity",
            "platform.notification.request",
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


def test_asyncapi_payload_reference_resolves_to_valid_cloud_event_schema() -> None:
    contract = load_yaml_mapping(ASYNCAPI_PATH)
    payload_reference = contract["components"]["messages"]["DomainEvent"]["payload"]["$ref"]
    resolved_payload_path = (ASYNCAPI_PATH.parent / payload_reference).resolve()

    assert contract["asyncapi"] == "3.0.0"
    assert contract["defaultContentType"] == "application/cloudevents+json"
    assert resolved_payload_path == CLOUD_EVENT_SCHEMA_PATH.resolve()
    assert resolved_payload_path.is_file()

    schema = json.loads(resolved_payload_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


def test_python_sdk_cloud_event_conforms_to_public_json_schema() -> None:
    schema = json.loads(CLOUD_EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    event = CloudEvent(
        source="urn:ai-hub:application:contract-test",
        type="company.example.record.changed.v1",
        subject="record/REC-001",
        data={"record_id": "REC-001", "aggregate_version": 1},
    )

    validator = cast(Validator, Draft202012Validator(schema))
    errors = list(validator.iter_errors(event.model_dump(mode="json", exclude_none=True)))
    assert not errors
