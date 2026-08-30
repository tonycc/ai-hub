"""Shared Pull/Push ingest contract registry helpers and JSON Schema validator."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing.exceptions import Unresolvable
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.ingest.service import IngestRecord
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig, TransportMode

LOGGER = logging.getLogger(__name__)
_REMOTE_REF_SCHEMES = ("http://", "https://", "file://", "ftp://")

ContractIssueCode = Literal[
    "contract_missing",
    "contract_not_active",
    "schema_mismatch",
    "schema_fingerprint_mismatch",
    "payload_too_large",
    "unknown_field",
    "validator_unavailable",
]


class ContractEnforcedError(ValueError):
    error_code = "ingest_contract_rejected"

    def __init__(self, message: str, issues: Sequence[ContractIssue]) -> None:
        self.issues = list(issues)
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RegisteredContract:
    source_application_id: str
    object_type: str
    contract_version: str
    json_schema: Mapping[str, Any]
    schema_fingerprint: str
    status: str
    origin: str = "MANUAL"
    field_classifications: Mapping[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class ContractIssue:
    code: ContractIssueCode
    message: str
    object_id: str | None = None
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ContractValidationResult:
    status: Literal["ok", "audit", "rejected"]
    issues: tuple[ContractIssue, ...]
    contract_version: str
    schema_fingerprint: str | None = None

    @property
    def rejected(self) -> bool:
        return self.status == "rejected"


def audit_summary_payload(issues: Sequence[ContractIssue]) -> dict[str, Any]:
    return {
        "mode": "AUDIT_ONLY",
        "issue_count": len(issues),
        "codes": [issue.code for issue in issues],
        "issues": [
            {
                "code": issue.code,
                "object_id": issue.object_id,
                "path": issue.path,
                "message": issue.message,
            }
            for issue in issues[:50]
        ],
    }


def canonical_json_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def schema_fingerprint(json_schema: Mapping[str, Any]) -> str:
    return canonical_json_digest(dict(json_schema))


def infer_draft_schema(
    payloads: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Build a conservative DRAFT schema: observed fields are optional.

    Mixed types, nested conflicts, and empty samples become warnings. Never
    infers business-required fields or additionalProperties=false.
    """
    if not payloads:
        return (
            {"type": "object", "properties": {}},
            ["sample coverage is empty; schema cannot be inferred"],
        )
    schema, warnings = _infer_object_properties(
        [dict(payload) for payload in payloads], path=""
    )
    return schema, warnings


def _infer_object_properties(
    objects: Sequence[Mapping[str, Any]], *, path: str
) -> tuple[dict[str, Any], list[str]]:
    by_key: dict[str, list[Any]] = {}
    for payload in objects:
        for key, value in payload.items():
            by_key.setdefault(str(key), []).append(value)
    properties: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for key, values in sorted(by_key.items()):
        field_path = key if not path else f"{path}.{key}"
        fragment, field_warnings = _infer_values(values, field_path)
        properties[key] = fragment
        warnings.extend(field_warnings)
    return {"type": "object", "properties": properties}, warnings


def _infer_values(values: Sequence[Any], path: str) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    types = {_json_type(value) for value in values}
    null_seen = "null" in types
    usable = {item for item in types if item != "null"}
    fragment: dict[str, Any] = {}
    if not usable:
        fragment["type"] = "null"
        warnings.append(f"field {path} was only observed as null")
    elif len(usable) == 1:
        declared: list[str] | str = next(iter(usable))
        if null_seen:
            declared = sorted({str(declared), "null"})
            warnings.append(
                f"field {path} observed as null in "
                f"{sum(1 for value in values if value is None)}/{len(values)} samples"
            )
        fragment["type"] = declared
    else:
        fragment["type"] = sorted(usable | ({"null"} if null_seen else set()))
        warnings.append(f"field {path} has mixed types {sorted(usable)}")
        if null_seen:
            warnings.append(
                f"field {path} observed as null in "
                f"{sum(1 for value in values if value is None)}/{len(values)} samples"
            )
    dicts = [
        cast(Mapping[str, Any], value) for value in values if isinstance(value, dict)
    ]
    if dicts:
        nested, nested_warnings = _infer_object_properties(dicts, path=path)
        fragment["properties"] = nested["properties"]
        warnings.extend(nested_warnings)
    lists = [cast(list[Any], value) for value in values if isinstance(value, list)]
    items = [item for value in lists for item in value]
    if items:
        item_schema, item_warnings = _infer_values(items, f"{path}[]")
        fragment["items"] = item_schema
        warnings.extend(item_warnings)
    return fragment, warnings


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _payload_size_bytes(payload: Mapping[str, Any] | None) -> int:
    if payload is None:
        return 0
    return len(
        json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode()
    )


def assert_closed_json_schema(json_schema: Mapping[str, Any]) -> None:
    """Reject remote $ref/$id so validators never retrieve http(s)/file URIs."""
    _assert_closed_schema_node(json_schema)


def _assert_closed_schema_node(node: Any) -> None:
    if isinstance(node, Mapping):
        mapping = cast(Mapping[str, Any], node)
        for key in ("$ref", "$dynamicRef", "$recursiveRef"):
            value = mapping.get(key)
            if isinstance(value, str) and not value.strip().startswith("#"):
                raise ValueError(
                    f"json_schema {key} must be an in-document fragment, not {value}"
                )
        identity = mapping.get("$id")
        if isinstance(identity, str) and _is_remote_schema_uri(identity):
            raise ValueError(f"json_schema $id must not use a remote URI: {identity}")
        for value in mapping.values():
            _assert_closed_schema_node(value)
        return
    if isinstance(node, list):
        for item in cast(list[Any], node):
            _assert_closed_schema_node(item)


def _is_remote_schema_uri(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered.startswith(_REMOTE_REF_SCHEMES):
        return True
    return "://" in lowered and not lowered.startswith("#")


def closed_draft202012_validator(
    json_schema: Mapping[str, Any],
) -> Draft202012Validator:
    payload = dict(json_schema)
    assert_closed_json_schema(payload)
    Draft202012Validator.check_schema(payload)
    return Draft202012Validator(payload)


class IngestContractValidator:
    """Validate normalized ingest records against a registered JSON Schema.

    AUDIT_ONLY never rejects Pull batches. ENFORCE (always for Push, and for
    Pull only when the source mode and the global gate are both on) rejects.
    """

    def __init__(self) -> None:
        self.audit_issue_counts: dict[tuple[str, str, str], int] = {}

    def validate_records(
        self,
        records: Sequence[IngestRecord],
        *,
        source: IngestSourceConfig,
        payload_contract_version: str,
        contract: RegisteredContract | None,
        payload_max_bytes: int,
        pull_enforcement_gate: bool = False,
        schema_fingerprint_header: str | None = None,
        accepted_statuses: Sequence[str] = ("ACTIVE",),
    ) -> ContractValidationResult:
        enforce = _should_enforce(
            source.transport_mode,
            source.contract_validation_mode,
            pull_enforcement_gate,
        )
        issues: list[ContractIssue] = []
        fingerprint = contract.schema_fingerprint if contract is not None else None
        allowed_statuses = frozenset(accepted_statuses)

        if contract is None:
            issues.append(
                ContractIssue(
                    "contract_missing",
                    "no ACTIVE ingest contract is registered for this source/version",
                )
            )
        elif contract.status not in allowed_statuses:
            issues.append(
                ContractIssue(
                    "contract_not_active",
                    f"ingest contract {contract.contract_version} status is {contract.status}",
                )
            )
        elif contract.contract_version != payload_contract_version:
            issues.append(
                ContractIssue(
                    "schema_mismatch",
                    "payload_contract_version does not match the registered ACTIVE contract",
                )
            )
        elif (
            schema_fingerprint_header is not None
            and schema_fingerprint_header != contract.schema_fingerprint
        ):
            issues.append(
                ContractIssue(
                    "schema_fingerprint_mismatch",
                    "schema_fingerprint does not match the registered ACTIVE contract",
                )
            )

        validator: Draft202012Validator | None = None
        if contract is not None and contract.status in allowed_statuses:
            try:
                validator = closed_draft202012_validator(contract.json_schema)
            except (SchemaError, ValueError, Unresolvable):
                issues.append(
                    ContractIssue(
                        "validator_unavailable",
                        "registered schema is invalid",
                    )
                )
                validator = None

        for record in records:
            if record.operation == "delete":
                continue
            size = _payload_size_bytes(record.payload)
            if size > payload_max_bytes:
                raise ContractEnforcedError(
                    "payload exceeds payload_max_bytes",
                    (
                        ContractIssue(
                            "payload_too_large",
                            f"payload exceeds {payload_max_bytes} bytes",
                            object_id=record.object_id,
                        ),
                    ),
                )
            if validator is None or record.payload is None:
                continue
            payload_obj = dict(record.payload)
            try:
                schema_errors = list(cast(Any, validator).iter_errors(payload_obj))
            except Exception:  # noqa: BLE001 - referencing/runtime validator faults
                issues.append(
                    ContractIssue(
                        "validator_unavailable",
                        "registered schema could not be evaluated",
                        object_id=record.object_id,
                    )
                )
                validator = None
                continue
            for error in schema_errors:
                issues.append(_safe_schema_issue(error, record.object_id))

        if not issues:
            return ContractValidationResult(
                "ok",
                (),
                payload_contract_version,
                fingerprint,
            )

        status: Literal["ok", "audit", "rejected"] = "rejected" if enforce else "audit"
        if not enforce:
            LOGGER.warning(
                json.dumps(
                    {
                        "event": "ingest_contract_audit",
                        "source_application_id": source.source_application_id,
                        "object_type": source.object_type,
                        "transport_mode": source.transport_mode,
                        "payload_contract_version": payload_contract_version,
                        "issue_count": len(issues),
                        "codes": [issue.code for issue in issues],
                    },
                    separators=(",", ":"),
                )
            )
            self._record_audit_metrics(source, issues)
        result = ContractValidationResult(
            status,
            tuple(issues),
            payload_contract_version,
            fingerprint,
        )
        if result.rejected:
            raise ContractEnforcedError(
                "ingest contract validation failed under ENFORCE",
                result.issues,
            )
        return result

    def _record_audit_metrics(
        self, source: IngestSourceConfig, issues: Sequence[ContractIssue]
    ) -> None:
        for issue in issues:
            key = (source.source_application_id, source.object_type, issue.code)
            self.audit_issue_counts[key] = self.audit_issue_counts.get(key, 0) + 1


def _safe_schema_issue(error: Any, object_id: str) -> ContractIssue:
    keyword = str(getattr(error, "validator", "schema") or "schema")
    path_parts = [str(part) for part in getattr(error, "absolute_path", ())]
    path = ".".join(path_parts) or None
    if keyword == "additionalProperties":
        return ContractIssue(
            "unknown_field",
            "payload contains unregistered fields",
            object_id=object_id,
            path=path,
        )
    return ContractIssue(
        "schema_mismatch",
        f"schema keyword {keyword} failed",
        object_id=object_id,
        path=path,
    )


def replay_payloads_against_schema(
    json_schema: Mapping[str, Any],
    records: Sequence[IngestRecord],
) -> tuple[ContractIssue, ...]:
    """Replay observation-window payloads against the target schema.

    Used by certification bind. Does not consult caller-supplied pass/fail
    flags, and does not require the contract row to already be ACTIVE.
    """
    try:
        validator = closed_draft202012_validator(json_schema)
    except (SchemaError, ValueError, Unresolvable):
        return (
            ContractIssue(
                "validator_unavailable",
                "registered schema is invalid",
            ),
        )
    issues: list[ContractIssue] = []
    for record in records:
        if record.operation == "delete" or record.payload is None:
            continue
        try:
            schema_errors = list(
                cast(Any, validator).iter_errors(dict(record.payload))
            )
        except Exception:  # noqa: BLE001 - referencing/runtime validator faults
            issues.append(
                ContractIssue(
                    "validator_unavailable",
                    "registered schema could not be evaluated",
                    object_id=record.object_id,
                )
            )
            continue
        for error in schema_errors:
            issues.append(_safe_schema_issue(error, record.object_id))
    return tuple(issues)


def _should_enforce(
    transport_mode: TransportMode,
    source_mode: str,
    pull_enforcement_gate: bool,
) -> bool:
    if transport_mode == "PUSH_AGENT":
        return True
    return source_mode == "ENFORCE" and pull_enforcement_gate


async def load_active_contract(
    session: AsyncSession,
    *,
    source_application_id: str,
    object_type: str,
    contract_version: str | None = None,
    statuses: Sequence[str] = ("ACTIVE",),
) -> RegisteredContract | None:
    allowed = tuple(statuses) or ("ACTIVE",)
    unknown = [
        status
        for status in allowed
        if status not in {"ACTIVE", "DEPRECATED", "DRAFT", "REJECTED"}
    ]
    if unknown:
        raise ValueError(f"unsupported ingest contract status filter: {unknown}")
    listed = ", ".join(f"'{status}'" for status in allowed)
    query = f"""
        SELECT source_application_id, object_type, contract_version, json_schema,
               schema_fingerprint, status, origin, field_classifications
        FROM platform_core.ingest_contract
        WHERE source_application_id = :source_application_id
          AND object_type = :object_type
          AND status IN ({listed})
    """
    params: dict[str, Any] = {
        "source_application_id": source_application_id,
        "object_type": object_type,
    }
    if contract_version is not None:
        query += " AND contract_version = :contract_version"
        params["contract_version"] = contract_version
    result = await session.execute(text(query), params)
    row = result.one_or_none()
    if row is None:
        return None
    schema = dict(row.json_schema)
    fingerprint = (
        str(row.schema_fingerprint) if row.schema_fingerprint else schema_fingerprint(schema)
    )
    return RegisteredContract(
        source_application_id=str(row.source_application_id),
        object_type=str(row.object_type),
        contract_version=str(row.contract_version),
        json_schema=schema,
        schema_fingerprint=fingerprint,
        status=str(row.status),
        origin=str(row.origin),
        field_classifications=dict(row.field_classifications or {}),
    )
