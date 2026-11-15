"""Shared ingest contract validator (C1-A)."""

from __future__ import annotations

import pytest
from ai_hub_platform.modules.ingest.contract import (
    ContractEnforcedError,
    IngestContractValidator,
    RegisteredContract,
    schema_fingerprint,
)
from ai_hub_platform.modules.ingest.service import IngestRecord
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig


def _pull(**overrides: object) -> IngestSourceConfig:
    payload: dict[str, object] = {
        "source_application_id": "standalone-example",
        "object_type": "device",
        "export_base_url": "http://app.test",
    }
    payload.update(overrides)
    return IngestSourceConfig.model_validate(payload)


def _push() -> IngestSourceConfig:
    return IngestSourceConfig.model_validate(
        {
            "source_application_id": "e10-adapter",
            "object_type": "erp.item",
            "transport_mode": "PUSH_AGENT",
            "push_protocol_version": "1",
            "contract_validation_mode": "ENFORCE",
        }
    )


def _contract(schema: dict[str, object] | None = None) -> RegisteredContract:
    json_schema = schema or {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    return RegisteredContract(
        source_application_id="standalone-example",
        object_type="device",
        contract_version="device.v1",
        json_schema=json_schema,
        schema_fingerprint=schema_fingerprint(json_schema),
        status="ACTIVE",
    )


def test_enforce_rejects_unknown_fields_and_accepts_valid_payload() -> None:
    validator = IngestContractValidator()
    contract = _contract()
    source = _pull(contract_validation_mode="ENFORCE")
    ok = validator.validate_records(
        [IngestRecord("E-1", "upsert", 1, {"name": "lathe"})],
        source=source,
        payload_contract_version="device.v1",
        contract=contract,
        payload_max_bytes=1024,
    )
    assert ok.status == "ok"
    with pytest.raises(ContractEnforcedError, match="contract validation") as caught:
        validator.validate_records(
            [IngestRecord("E-1", "upsert", 1, {"name": "lathe", "secret": "x"})],
            source=source,
            payload_contract_version="device.v1",
            contract=contract,
            payload_max_bytes=1024,
        )
    assert any(issue.code == "unknown_field" for issue in caught.value.issues)
    assert all("secret" not in issue.message for issue in caught.value.issues)


def test_delete_records_skip_schema_validation() -> None:
    validator = IngestContractValidator()
    result = validator.validate_records(
        [IngestRecord("E-1", "delete", 2, None)],
        source=_pull(contract_validation_mode="ENFORCE"),
        payload_contract_version="device.v1",
        contract=_contract(),
        payload_max_bytes=1024,
    )
    assert result.status == "ok"


def test_closed_schema_rejects_remote_refs_and_does_not_retrieve() -> None:
    from ai_hub_platform.modules.ingest.contract import (
        assert_closed_json_schema,
        closed_draft202012_validator,
        replay_payloads_against_schema,
    )

    remote = {
        "type": "object",
        "properties": {"name": {"$ref": "https://example.invalid/name.json"}},
    }
    with pytest.raises(ValueError, match="fragment"):
        assert_closed_json_schema(remote)
    with pytest.raises(ValueError, match="fragment"):
        closed_draft202012_validator(remote)
    issues = replay_payloads_against_schema(
        remote, [IngestRecord("E-1", "upsert", 1, {"name": "a"})]
    )
    assert any(issue.code == "validator_unavailable" for issue in issues)

    validator = IngestContractValidator()
    result = validator.validate_records(
        [IngestRecord("E-1", "upsert", 1, {"name": "a"})],
        source=_pull(),
        payload_contract_version="device.v1",
        contract=_contract(
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        payload_max_bytes=1024,
    )
    assert result.status == "ok"


def test_deprecated_contract_still_enforces_schema_when_accepted() -> None:
    validator = IngestContractValidator()
    contract = RegisteredContract(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema=_contract().json_schema,
        schema_fingerprint=_contract().schema_fingerprint,
        status="DEPRECATED",
    )
    with pytest.raises(ContractEnforcedError) as caught:
        validator.validate_records(
            [IngestRecord("I-1", "upsert", 1, {"name": "ok", "secret": True})],
            source=_push(),
            payload_contract_version="item.v1",
            contract=contract,
            payload_max_bytes=1024,
            accepted_statuses=("ACTIVE", "DEPRECATED"),
        )
    assert any(issue.code == "unknown_field" for issue in caught.value.issues)
