"""Shared ingest contract validator (C1-A)."""

from __future__ import annotations

from typing import Any

import pytest
from ai_hub_platform.modules.ingest.contract import (
    ContractEnforcedError,
    IngestContractValidator,
    RegisteredContract,
    infer_draft_schema,
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


def test_audit_only_pull_never_rejects_missing_or_invalid_contract() -> None:
    validator = IngestContractValidator()
    result = validator.validate_records(
        [IngestRecord("E-1", "upsert", 1, {"name": 1, "extra": True})],
        source=_pull(),
        payload_contract_version="device.v1",
        contract=None,
        payload_max_bytes=1024,
        pull_enforcement_gate=True,
    )
    assert result.status == "audit"
    assert any(issue.code == "contract_missing" for issue in result.issues)


def test_pull_enforce_requires_global_gate() -> None:
    validator = IngestContractValidator()
    source = _pull(contract_validation_mode="ENFORCE")
    records = [IngestRecord("E-1", "upsert", 1, {"name": "ok"})]
    audited = validator.validate_records(
        records,
        source=source,
        payload_contract_version="device.v1",
        contract=None,
        payload_max_bytes=1024,
        pull_enforcement_gate=False,
    )
    assert audited.status == "audit"
    with pytest.raises(ContractEnforcedError):
        validator.validate_records(
            records,
            source=source,
            payload_contract_version="device.v1",
            contract=None,
            payload_max_bytes=1024,
            pull_enforcement_gate=True,
        )


def test_push_always_enforces_even_without_pull_gate() -> None:
    validator = IngestContractValidator()
    with pytest.raises(ContractEnforcedError):
        validator.validate_records(
            [IngestRecord("I-1", "upsert", 1, {"name": "ok"})],
            source=_push(),
            payload_contract_version="item.v1",
            contract=None,
            payload_max_bytes=1024,
            pull_enforcement_gate=False,
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
        pull_enforcement_gate=True,
    )
    assert ok.status == "ok"
    with pytest.raises(ContractEnforcedError, match="ENFORCE") as caught:
        validator.validate_records(
            [IngestRecord("E-1", "upsert", 1, {"name": "lathe", "secret": "x"})],
            source=source,
            payload_contract_version="device.v1",
            contract=contract,
            payload_max_bytes=1024,
            pull_enforcement_gate=True,
        )
    assert any(issue.code == "unknown_field" for issue in caught.value.issues)
    assert all("secret" not in issue.message for issue in caught.value.issues)


def test_audit_only_still_hard_rejects_oversized_payload() -> None:
    validator = IngestContractValidator()
    with pytest.raises(ContractEnforcedError) as caught:
        validator.validate_records(
            [IngestRecord("E-1", "upsert", 1, {"name": "x" * 80})],
            source=_pull(),
            payload_contract_version="device.v1",
            contract=_contract(),
            payload_max_bytes=16,
            pull_enforcement_gate=False,
        )
    assert any(issue.code == "payload_too_large" for issue in caught.value.issues)


def test_runtime_validator_fault_audits_for_pull_and_rejects_for_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.modules.ingest import contract as contract_mod

    class _Broken:
        @staticmethod
        def check_schema(schema: object) -> None:
            del schema

        def __init__(self, schema: object, **kwargs: object) -> None:
            del schema, kwargs

        def iter_errors(self, instance: object, _schema: object = None) -> Any:
            del instance, _schema
            raise RuntimeError("unresolvable ref")

    monkeypatch.setattr(contract_mod, "Draft202012Validator", _Broken)
    validator = IngestContractValidator()
    records = [IngestRecord("E-1", "upsert", 1, {"name": "lathe"})]
    audited = validator.validate_records(
        records,
        source=_pull(),
        payload_contract_version="device.v1",
        contract=_contract(),
        payload_max_bytes=1024,
    )
    assert audited.status == "audit"
    assert any(issue.code == "validator_unavailable" for issue in audited.issues)
    assert all("unresolvable" not in issue.message for issue in audited.issues)
    with pytest.raises(ContractEnforcedError) as caught:
        validator.validate_records(
            records,
            source=_push(),
            payload_contract_version="device.v1",
            contract=_contract(),
            payload_max_bytes=1024,
        )
    assert any(issue.code == "validator_unavailable" for issue in caught.value.issues)


def test_delete_records_skip_schema_validation() -> None:
    validator = IngestContractValidator()
    result = validator.validate_records(
        [IngestRecord("E-1", "delete", 2, None)],
        source=_pull(contract_validation_mode="ENFORCE"),
        payload_contract_version="device.v1",
        contract=_contract(),
        payload_max_bytes=1024,
        pull_enforcement_gate=True,
    )
    assert result.status == "ok"


def test_infer_draft_schema_keeps_fields_optional_and_warns_on_mixed_types() -> None:
    schema, warnings = infer_draft_schema(
        [
            {"name": "a", "qty": 1},
            {"name": "b", "qty": "2"},
        ]
    )
    assert schema["type"] == "object"
    assert "required" not in schema
    assert schema["properties"]["name"] == {"type": "string"}
    assert any("qty" in warning for warning in warnings)

    nested, nested_warnings = infer_draft_schema(
        [
            {"a": {"x": 1}},
            {"a": {"x": "two"}},
        ]
    )
    assert nested["properties"]["a"]["type"] == "object"
    assert nested["properties"]["a"]["properties"]["x"]["type"] == [
        "integer",
        "string",
    ]
    assert any("a.x" in warning for warning in nested_warnings)

    nullable, null_warnings = infer_draft_schema(
        [
            {"name": None},
            {"name": "a"},
        ]
    )
    assert nullable["properties"]["name"] == {"type": ["null", "string"]}
    assert any("null" in warning for warning in null_warnings)
    assert "required" not in nullable


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


def test_audit_only_records_metrics_and_summary_payload() -> None:
    from ai_hub_platform.modules.ingest.contract import audit_summary_payload

    validator = IngestContractValidator()
    result = validator.validate_records(
        [IngestRecord("E-1", "upsert", 1, {"secret": True})],
        source=_pull(),
        payload_contract_version="device.v1",
        contract=_contract(),
        payload_max_bytes=1024,
    )
    assert result.status == "audit"
    key = ("standalone-example", "device", "unknown_field")
    assert validator.audit_issue_counts[key] >= 1
    summary = audit_summary_payload(result.issues)
    assert summary["mode"] == "AUDIT_ONLY"
    assert summary["issue_count"] == len(result.issues)
    assert summary["issues"][0]["code"] == "unknown_field"


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
