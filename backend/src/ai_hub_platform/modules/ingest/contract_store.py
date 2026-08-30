"""Portal-managed ingest_contract and certification lifecycle (design §4.2)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_hub_platform.modules.conformance.service import unpack_data_ingest_check_evidence
from ai_hub_platform.modules.ingest.config_store import IngestConfigStore
from ai_hub_platform.modules.ingest.contract import (
    ContractIssue,
    assert_closed_json_schema,
    canonical_json_digest,
    infer_draft_schema,
    replay_payloads_against_schema,
    schema_fingerprint,
)
from ai_hub_platform.modules.ingest.service import IngestRecord, Operation
from ai_hub_platform.modules.ingest.source_lock import lock_ingest_source
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig

ApproveRole = Literal["data_owner", "operator"]
CERTIFICATION_PASSED = "passed"
OBSERVATION_REPLAY_MAX_ROWS = 10_000
CERTIFICATION_KIND_BY_SLOT = {
    "full_regression_evidence_ref": "full_regression",
    "incremental_regression_evidence_ref": "incremental_regression",
    "rollback_drill_evidence_ref": "rollback_drill",
}


class IngestContractError(ValueError):
    error_code = "invalid_ingest_contract"


class IngestContractConflictError(IngestContractError):
    error_code = "ingest_contract_conflict"


def _empty_json_object() -> dict[str, Any]:
    return {}


@dataclass(frozen=True, slots=True)
class IngestContractRow:
    source_application_id: str
    object_type: str
    contract_version: str
    json_schema: dict[str, Any]
    schema_fingerprint: str
    field_classifications: dict[str, Any]
    compatibility_mode: str
    origin: str
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    updated_at: datetime
    inference_evidence_ref: str | None = None


@dataclass(frozen=True, slots=True)
class IngestCertificationRow:
    certification_id: UUID
    source_application_id: str
    object_type: str
    contract_version: str
    schema_fingerprint: str
    rows_validated: int
    full_regression_status: str | None
    incremental_regression_status: str | None
    rollback_drill_status: str | None
    data_owner_approved_by: str | None
    data_owner_approved_at: datetime | None
    operator_approved_by: str | None
    operator_approved_at: datetime | None
    status: str
    updated_at: datetime
    observation_batch_from: UUID | None = None
    observation_batch_to: UUID | None = None
    violation_summary: dict[str, Any] = field(default_factory=_empty_json_object)
    exemption_summary: dict[str, Any] = field(default_factory=_empty_json_object)
    full_regression_evidence_ref: str | None = None
    incremental_regression_evidence_ref: str | None = None
    rollback_drill_evidence_ref: str | None = None
    transport_mode: str = "PULL_EXPORT"


def _require_object_schema(json_schema: dict[str, Any]) -> dict[str, Any]:
    payload = dict(json_schema)
    try:
        assert_closed_json_schema(payload)
        Draft202012Validator.check_schema(payload)
    except SchemaError as error:
        raise IngestContractError(
            f"json_schema is not a valid JSON Schema: {error.message}"
        ) from error
    except ValueError as error:
        raise IngestContractError(str(error)) from error
    if payload.get("type") != "object":
        raise IngestContractError("json_schema type must be object")
    properties = payload.get("properties", {})
    if properties is not None and not isinstance(properties, dict):
        raise IngestContractError("json_schema properties must be an object")
    return payload


def _assert_certification_evidence(
    *,
    rows_validated: int,
    full_regression_status: str | None,
    incremental_regression_status: str | None,
    rollback_drill_status: str | None,
    observation_batch_from: UUID | None,
    observation_batch_to: UUID | None,
    violation_summary: dict[str, Any] | None,
    exemption_summary: dict[str, Any] | None,
    full_regression_evidence_ref: str | None,
    incremental_regression_evidence_ref: str | None,
    rollback_drill_evidence_ref: str | None,
) -> None:
    if rows_validated < 1:
        raise IngestContractError("certification requires rows_validated >= 1")
    if observation_batch_from is None or observation_batch_to is None:
        raise IngestContractError(
            "certification requires an observation batch window"
        )
    if not isinstance(violation_summary, dict):
        raise IngestContractError("violation_summary must be an object")
    unexempted = violation_summary.get("unexempted")
    if not isinstance(unexempted, list) or unexempted:
        raise IngestContractError(
            "observation window still has unexempted violations"
        )
    if not isinstance(exemption_summary, dict):
        raise IngestContractError("exemption_summary must be an object")
    for name, value in (
        ("full_regression_status", full_regression_status),
        ("incremental_regression_status", incremental_regression_status),
        ("rollback_drill_status", rollback_drill_status),
    ):
        if value != CERTIFICATION_PASSED:
            raise IngestContractError(f"{name} must be {CERTIFICATION_PASSED}")
    for name, value in (
        ("full_regression_evidence_ref", full_regression_evidence_ref),
        ("incremental_regression_evidence_ref", incremental_regression_evidence_ref),
        ("rollback_drill_evidence_ref", rollback_drill_evidence_ref),
    ):
        if not isinstance(value, str) or not value.strip():
            raise IngestContractError(
                f"{name} must be a non-empty traceable evidence reference"
            )


async def _registered_transport_mode(
    session: AsyncSession,
    *,
    source_application_id: str,
    object_type: str,
) -> str:
    source = await IngestConfigStore().get_source(
        session,
        source_application_id=source_application_id,
        object_type=object_type,
    )
    if source is None:
        raise IngestContractConflictError(
            "certification requires a registered ingest source"
        )
    return source.config.transport_mode


def _parse_evidence_run_id(name: str, value: str) -> UUID:
    try:
        return UUID(value.strip())
    except ValueError as error:
        raise IngestContractError(
            f"{name} must be a conformance_run UUID"
        ) from error


@dataclass(frozen=True, slots=True)
class BoundCertificationEvidence:
    rows_validated: int
    violation_summary: dict[str, Any]


def _issue_as_dict(issue: ContractIssue) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": issue.code,
        "message": issue.message,
    }
    if issue.object_id is not None:
        payload["object_id"] = issue.object_id
    if issue.path is not None:
        payload["path"] = issue.path
    return payload


def _issue_is_exempted(
    issue: ContractIssue, exemption_summary: dict[str, Any]
) -> bool:
    items = exemption_summary.get("items")
    if not isinstance(items, list):
        return False
    for raw_item in cast(list[Any], items):
        if not isinstance(raw_item, dict):
            continue
        mapping = cast(dict[str, Any], raw_item)
        if mapping.get("code") != issue.code:
            continue
        exempt_path = mapping.get("path")
        if exempt_path not in {None, ""}:
            if exempt_path != issue.path:
                continue
        exempt_object = mapping.get("object_id")
        if exempt_object in {None, "", issue.object_id}:
            return True
    return False


def _profiles_contain_data_ingest(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return "DATA_INGEST" in value
    try:
        return "DATA_INGEST" in [str(item) for item in value]
    except TypeError:
        return False


def _load_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return cast(dict[str, Any], value)
    if isinstance(value, str) and value.strip():
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return cast(dict[str, Any], loaded)
    return {}


def _parse_aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    if isinstance(value, str) and value.strip():
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _assert_evidence_covers_observation_window(
    wrapper: Mapping[str, Any],
    *,
    completed_at: Any,
    window_end: datetime,
) -> None:
    now = datetime.now(UTC)
    expires_at = _parse_aware_datetime(wrapper.get("expires_at"))
    if expires_at is not None and expires_at <= now:
        raise IngestContractError("conformance evidence has expired")
    verified_at = _parse_aware_datetime(wrapper.get("verified_at"))
    if verified_at is None or verified_at < window_end:
        raise IngestContractError(
            "conformance evidence must cover the observation window"
        )
    finished_at = _parse_aware_datetime(completed_at)
    if finished_at is not None and finished_at < window_end:
        raise IngestContractError(
            "conformance evidence must cover the observation window"
        )


def _record_operation(value: Any) -> Operation:
    operation = str(value or "upsert")
    if operation not in {"upsert", "delete"}:
        raise IngestContractError(
            "observation window contains an unsupported change operation"
        )
    return cast(Operation, operation)


async def bind_certification_evidence(
    session: AsyncSession,
    *,
    source_application_id: str,
    object_type: str,
    schema_fingerprint: str,
    json_schema: dict[str, Any],
    rows_validated: int,
    observation_batch_from: UUID,
    observation_batch_to: UUID,
    exemption_summary: dict[str, Any],
    full_regression_evidence_ref: str,
    incremental_regression_evidence_ref: str,
    rollback_drill_evidence_ref: str,
    transport_mode: str,
) -> BoundCertificationEvidence:
    """Replay the observation window and bind DATA_INGEST conformance evidence."""
    batch_result = await session.execute(
        text(
            """
            SELECT batch_id, source_application_id, object_type, status,
                   schema_fingerprint, started_at, finished_at, transport_mode
            FROM platform_raw.raw_ingest_batch
            WHERE batch_id IN (:batch_from, :batch_to)
            """
        ),
        {
            "batch_from": observation_batch_from,
            "batch_to": observation_batch_to,
        },
    )
    batches = {UUID(str(row.batch_id)): row for row in batch_result.all()}
    expected_ids = {observation_batch_from, observation_batch_to}
    if set(batches) != expected_ids:
        raise IngestContractError(
            "observation window must reference loaded Raw ingest batches"
        )
    started_at: list[datetime] = []
    finished_at: list[datetime] = []
    for batch in batches.values():
        if str(batch.source_application_id) != source_application_id:
            raise IngestContractError(
                "observation batches must belong to the certified source"
            )
        if str(batch.object_type) != object_type:
            raise IngestContractError(
                "observation batches must match the certified object_type"
            )
        if str(batch.status) != "loaded":
            raise IngestContractError(
                "observation batches must be in loaded status"
            )
        if str(batch.transport_mode) != transport_mode:
            raise IngestContractError(
                "observation batches must match the source transport_mode"
            )
        fingerprint = batch.schema_fingerprint
        if fingerprint is None or str(fingerprint) != schema_fingerprint:
            raise IngestContractError(
                "observation batches must carry the certified schema_fingerprint"
            )
        started_at.append(batch.started_at)
        completed = batch.finished_at
        if completed is None:
            raise IngestContractError(
                "observation batches must have finished_at"
            )
        finished_at.append(completed)
    window_start = min(started_at)
    started_end = max(started_at)
    window_end = max(finished_at)
    record_result = await session.execute(
        text(
            """
            SELECT record.object_id, record.operation, record.version, record.payload
            FROM platform_raw.raw_change_record AS record
            JOIN platform_raw.raw_ingest_batch AS batch
              ON batch.batch_id = record.batch_id
            WHERE batch.source_application_id = :source_application_id
              AND batch.object_type = :object_type
              AND batch.status = 'loaded'
              AND batch.schema_fingerprint = :schema_fingerprint
              AND batch.schema_fingerprint IS NOT NULL
              AND batch.transport_mode = :transport_mode
              AND batch.started_at >= :window_start
              AND batch.started_at <= :window_end
            ORDER BY record.version, record.object_id
            """
        ),
        {
            "source_application_id": source_application_id,
            "object_type": object_type,
            "schema_fingerprint": schema_fingerprint,
            "transport_mode": transport_mode,
            "window_start": window_start,
            "window_end": started_end,
        },
    )
    records: list[IngestRecord] = []
    for row in record_result.all():
        payload = row.payload
        records.append(
            IngestRecord(
                str(row.object_id),
                _record_operation(row.operation),
                int(row.version),
                dict(cast(dict[str, Any], payload)) if isinstance(payload, dict) else None,
            )
        )
    actual_rows = len(records)
    if actual_rows < 1:
        raise IngestContractError(
            "observation window does not contain any Raw change records"
        )
    if actual_rows != rows_validated:
        raise IngestContractError(
            "rows_validated does not match the observation window record count"
        )
    if actual_rows > OBSERVATION_REPLAY_MAX_ROWS:
        raise IngestContractError(
            "observation window is too large to replay against the target schema"
        )
    observed = replay_payloads_against_schema(json_schema, records)
    exempted = [issue for issue in observed if _issue_is_exempted(issue, exemption_summary)]
    unexempted = [
        issue for issue in observed if not _issue_is_exempted(issue, exemption_summary)
    ]
    violation_summary = {
        "unexempted": [_issue_as_dict(issue) for issue in unexempted],
        "observed": [_issue_as_dict(issue) for issue in observed],
        "exempted": [_issue_as_dict(issue) for issue in exempted],
    }
    if unexempted:
        raise IngestContractError(
            "observation window still has unexempted contract violations"
        )
    slot_refs = {
        "full_regression_evidence_ref": full_regression_evidence_ref,
        "incremental_regression_evidence_ref": incremental_regression_evidence_ref,
        "rollback_drill_evidence_ref": rollback_drill_evidence_ref,
    }
    run_ids = {
        slot: _parse_evidence_run_id(slot, value) for slot, value in slot_refs.items()
    }
    if len(set(run_ids.values())) != 3:
        raise IngestContractError(
            "regression evidence refs must identify three distinct conformance runs"
        )
    run_result = await session.execute(
        text(
            """
            SELECT r.run_id, r.application_id, r.status AS run_status,
                   r.requested_profiles, r.completed_at, c.profile, c.status AS check_status,
                   c.evidence
            FROM platform_core.conformance_run AS r
            JOIN platform_core.conformance_check AS c
              ON c.run_id = r.run_id
            WHERE r.run_id IN (:run_a, :run_b, :run_c)
              AND c.profile = 'DATA_INGEST'
            """
        ),
        dict(
            zip(("run_a", "run_b", "run_c"), sorted(set(run_ids.values())), strict=True)
        ),
    )
    checks_by_run: dict[UUID, list[Any]] = {}
    for row in run_result.all():
        checks_by_run.setdefault(UUID(str(row.run_id)), []).append(row)
    if set(checks_by_run) != set(run_ids.values()):
        raise IngestContractError(
            "evidence refs must identify existing DATA_INGEST conformance checks"
        )
    for slot, run_id in run_ids.items():
        expected_kind = CERTIFICATION_KIND_BY_SLOT[slot]
        rows = checks_by_run[run_id]
        passed = [
            row
            for row in rows
            if str(row.run_status) == "PASSED" and str(row.check_status) == "PASSED"
        ]
        if not passed:
            raise IngestContractError(
                "conformance evidence runs must have PASSED status"
            )
        row = passed[0]
        if str(row.application_id) != source_application_id:
            raise IngestContractError(
                "conformance evidence must belong to the certified source application"
            )
        if not _profiles_contain_data_ingest(row.requested_profiles):
            raise IngestContractError(
                "conformance evidence must request the DATA_INGEST profile"
            )
        evidence = unpack_data_ingest_check_evidence(
            _load_json_object(row.evidence), object_type
        )
        wrapper = _load_json_object(row.evidence)
        _assert_evidence_covers_observation_window(
            wrapper,
            completed_at=row.completed_at,
            window_end=window_end,
        )
        if str(evidence.get("object_type") or "") != object_type:
            raise IngestContractError(
                "conformance evidence must identify the certified object_type"
            )
        if str(evidence.get("schema_fingerprint") or "") != schema_fingerprint:
            raise IngestContractError(
                "conformance evidence must match the certified schema_fingerprint"
            )
        if str(evidence.get("certification_kind") or "") != expected_kind:
            raise IngestContractError(
                "conformance evidence must identify full_regression, "
                "incremental_regression, or rollback_drill"
            )
        declared_mode = evidence.get("transport_mode")
        if declared_mode not in {None, transport_mode}:
            raise IngestContractError(
                "conformance evidence transport_mode does not match the source"
            )
        if transport_mode == "PUSH_AGENT" and declared_mode != "PUSH_AGENT":
            raise IngestContractError(
                "conformance evidence transport_mode does not match the source"
            )
    return BoundCertificationEvidence(
        rows_validated=actual_rows,
        violation_summary=violation_summary,
    )


def _source_requires_activation_certification(config: IngestSourceConfig) -> bool:
    if not config.enabled:
        return False
    if config.transport_mode == "PUSH_AGENT":
        return True
    return (
        config.transport_mode == "PULL_EXPORT"
        and config.contract_validation_mode == "ENFORCE"
    )


async def _assert_no_active_push_generation(
    session: AsyncSession,
    *,
    source_application_id: str,
    object_type: str,
) -> None:
    result = await session.execute(
        text(
            """
            SELECT generation_id
            FROM platform_raw.raw_push_generation
            WHERE source_application_id = :source_application_id
              AND object_type = :object_type
              AND status IN ('OPEN', 'RECEIVING', 'COMPLETING')
            LIMIT 1
            """
        ),
        {
            "source_application_id": source_application_id,
            "object_type": object_type,
        },
    )
    if result.one_or_none() is not None:
        raise IngestContractConflictError(
            "cannot activate a contract while a push generation is in progress"
        )


async def _assert_activation_certification(
    session: AsyncSession,
    contract: IngestContractRow,
) -> None:
    row = await IngestConfigStore().get_source(
        session,
        source_application_id=contract.source_application_id,
        object_type=contract.object_type,
    )
    if row is None or not _source_requires_activation_certification(row.config):
        return
    result = await session.execute(
        text(
            """
            SELECT certification_id
            FROM platform_core.ingest_contract_certification
            WHERE source_application_id = :source_application_id
              AND object_type = :object_type
              AND contract_version = :contract_version
              AND schema_fingerprint = :schema_fingerprint
              AND status = 'APPROVED'
              AND transport_mode = :transport_mode
            LIMIT 1
            """
        ),
        {
            "source_application_id": contract.source_application_id,
            "object_type": contract.object_type,
            "contract_version": contract.contract_version,
            "schema_fingerprint": contract.schema_fingerprint,
            "transport_mode": row.config.transport_mode,
        },
    )
    if result.one_or_none() is None:
        raise IngestContractConflictError(
            "enabled PUSH_AGENT and Pull ENFORCE require an APPROVED "
            "certification for this contract fingerprint; disable the source "
            "or revert Pull to AUDIT_ONLY first"
        )


def _row_to_contract(row: Any) -> IngestContractRow:
    return IngestContractRow(
        source_application_id=str(row.source_application_id),
        object_type=str(row.object_type),
        contract_version=str(row.contract_version),
        json_schema=dict(row.json_schema),
        schema_fingerprint=str(row.schema_fingerprint),
        field_classifications=dict(row.field_classifications or {}),
        compatibility_mode=str(row.compatibility_mode),
        origin=str(row.origin),
        status=str(row.status),
        reviewed_by=None if row.reviewed_by is None else str(row.reviewed_by),
        reviewed_at=row.reviewed_at,
        updated_at=row.updated_at,
        inference_evidence_ref=(
            None
            if getattr(row, "inference_evidence_ref", None) is None
            else str(row.inference_evidence_ref)
        ),
    )


def _row_to_certification(row: Any) -> IngestCertificationRow:
    return IngestCertificationRow(
        certification_id=cast(UUID, row.certification_id),
        source_application_id=str(row.source_application_id),
        object_type=str(row.object_type),
        contract_version=str(row.contract_version),
        schema_fingerprint=str(row.schema_fingerprint),
        rows_validated=int(row.rows_validated),
        full_regression_status=row.full_regression_status,
        incremental_regression_status=row.incremental_regression_status,
        rollback_drill_status=row.rollback_drill_status,
        data_owner_approved_by=(
            None
            if row.data_owner_approved_by is None
            else str(row.data_owner_approved_by)
        ),
        data_owner_approved_at=row.data_owner_approved_at,
        operator_approved_by=(
            None if row.operator_approved_by is None else str(row.operator_approved_by)
        ),
        operator_approved_at=row.operator_approved_at,
        status=str(row.status),
        updated_at=row.updated_at,
        observation_batch_from=row.observation_batch_from,
        observation_batch_to=row.observation_batch_to,
        violation_summary=dict(row.violation_summary or {}),
        exemption_summary=dict(row.exemption_summary or {}),
        full_regression_evidence_ref=row.full_regression_evidence_ref,
        incremental_regression_evidence_ref=row.incremental_regression_evidence_ref,
        rollback_drill_evidence_ref=row.rollback_drill_evidence_ref,
        transport_mode=str(getattr(row, "transport_mode", "PULL_EXPORT") or "PULL_EXPORT"),
    )


_CONTRACT_COLUMNS = """
    source_application_id, object_type, contract_version, json_schema,
    schema_fingerprint, field_classifications, compatibility_mode, origin,
    status, reviewed_by, reviewed_at, updated_at, inference_evidence_ref
"""

_CERT_COLUMNS = """
    certification_id, source_application_id, object_type, contract_version,
    schema_fingerprint, rows_validated, full_regression_status,
    incremental_regression_status, rollback_drill_status,
    data_owner_approved_by, data_owner_approved_at,
    operator_approved_by, operator_approved_at, status, updated_at,
    observation_batch_from, observation_batch_to, violation_summary,
    exemption_summary, full_regression_evidence_ref,
    incremental_regression_evidence_ref, rollback_drill_evidence_ref,
    transport_mode
"""


class IngestContractStore:
    async def list_contracts(self, session: AsyncSession) -> list[IngestContractRow]:
        result = await session.execute(
            text(
                f"""
                SELECT {_CONTRACT_COLUMNS}
                FROM platform_core.ingest_contract
                ORDER BY source_application_id, object_type, contract_version
                """
            )
        )
        return [_row_to_contract(row) for row in result.all()]

    async def get_contract(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        contract_version: str,
        for_update: bool = False,
    ) -> IngestContractRow | None:
        lock = " FOR UPDATE" if for_update else ""
        result = await session.execute(
            text(
                f"""
                SELECT {_CONTRACT_COLUMNS}
                FROM platform_core.ingest_contract
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND contract_version = :contract_version
                {lock}
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "contract_version": contract_version,
            },
        )
        row = result.one_or_none()
        return None if row is None else _row_to_contract(row)

    async def save_draft(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        contract_version: str,
        json_schema: dict[str, Any],
        field_classifications: dict[str, Any] | None = None,
        compatibility_mode: str = "BACKWARD",
        origin: str = "MANUAL",
        inference_evidence_ref: str | None = None,
    ) -> IngestContractRow:
        schema = _require_object_schema(json_schema)
        fingerprint = schema_fingerprint(schema)
        if compatibility_mode not in {"BACKWARD", "FORWARD", "FULL", "NONE"}:
            raise IngestContractError("unsupported compatibility_mode")
        if origin not in {"MANUAL", "INFERRED_FROM_RAW"}:
            raise IngestContractError("unsupported origin")
        await lock_ingest_source(session, source_application_id, object_type)
        existing = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
        )
        if existing is not None and existing.status != "DRAFT":
            raise IngestContractConflictError(
                "only DRAFT contracts can be overwritten"
            )
        await session.execute(
            text(
                """
                INSERT INTO platform_core.ingest_contract (
                    source_application_id, object_type, contract_version,
                    json_schema, schema_fingerprint, field_classifications,
                    compatibility_mode, origin, status, inference_evidence_ref
                ) VALUES (
                    :source_application_id, :object_type, :contract_version,
                    CAST(:json_schema AS jsonb), :schema_fingerprint,
                    CAST(:field_classifications AS jsonb),
                    :compatibility_mode, :origin, 'DRAFT',
                    :inference_evidence_ref
                )
                ON CONFLICT (source_application_id, object_type, contract_version)
                DO UPDATE
                SET json_schema = EXCLUDED.json_schema,
                    schema_fingerprint = EXCLUDED.schema_fingerprint,
                    field_classifications = EXCLUDED.field_classifications,
                    compatibility_mode = EXCLUDED.compatibility_mode,
                    origin = EXCLUDED.origin,
                    inference_evidence_ref = EXCLUDED.inference_evidence_ref,
                    updated_at = CURRENT_TIMESTAMP
                WHERE platform_core.ingest_contract.status = 'DRAFT'
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "contract_version": contract_version,
                "json_schema": json.dumps(schema, sort_keys=True),
                "schema_fingerprint": fingerprint,
                "field_classifications": json.dumps(
                    field_classifications or {}, sort_keys=True
                ),
                "compatibility_mode": compatibility_mode,
                "origin": origin,
                "inference_evidence_ref": inference_evidence_ref,
            },
        )
        stored = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
        )
        if stored is None:  # pragma: no cover
            raise IngestContractError("failed to persist ingest contract")
        return stored

    async def infer_draft_from_raw(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        contract_version: str,
        sample_limit: int = 200,
    ) -> IngestContractRow:
        sample_params = {
            "source_application_id": source_application_id,
            "object_type": object_type,
            "payload_contract_version": contract_version,
            "sample_limit": sample_limit,
        }
        current_result = await session.execute(
            text(
                """
                SELECT object_id, payload, version, updated_at
                FROM platform_raw.raw_current_state
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND payload_contract_version = :payload_contract_version
                  AND payload IS NOT NULL
                ORDER BY object_id
                LIMIT :sample_limit
                """
            ),
            sample_params,
        )
        change_result = await session.execute(
            text(
                """
                SELECT DISTINCT ON (record.object_id)
                    record.object_id, record.payload, record.version, record.batch_id
                FROM platform_raw.raw_change_record AS record
                JOIN platform_raw.raw_ingest_batch AS batch
                  ON batch.batch_id = record.batch_id
                WHERE record.source_application_id = :source_application_id
                  AND record.object_type = :object_type
                  AND record.payload_contract_version = :payload_contract_version
                  AND record.operation <> 'delete'
                  AND record.payload IS NOT NULL
                  AND batch.status = 'loaded'
                  AND COALESCE(batch.purpose, 'production') = 'production'
                ORDER BY record.object_id, record.version DESC
                LIMIT :sample_limit
                """
            ),
            sample_params,
        )
        current_rows = current_result.all()
        change_rows = change_result.all()
        samples_by_object: dict[str, dict[str, Any]] = {}
        change_batch_by_key: dict[tuple[str, int], str] = {}
        current_updated_at: list[datetime] = []
        change_versions: list[int] = []
        for row in change_rows:
            payload = row.payload
            if not isinstance(payload, dict):
                continue
            object_id = str(row.object_id)
            version = int(row.version)
            change_versions.append(version)
            batch_id = None if row.batch_id is None else str(row.batch_id)
            if batch_id is not None:
                change_batch_by_key[(object_id, version)] = batch_id
            if object_id in samples_by_object:
                continue
            samples_by_object[object_id] = {
                "object_id": object_id,
                "payload": cast(dict[str, Any], payload),
                "version": version,
                "batch_id": batch_id,
                "origin": "change_record",
            }
        for row in current_rows:
            payload = row.payload
            if not isinstance(payload, dict):
                continue
            object_id = str(row.object_id)
            version = int(row.version)
            updated_at = row.updated_at
            if isinstance(updated_at, datetime):
                current_updated_at.append(updated_at)
            samples_by_object[object_id] = {
                "object_id": object_id,
                "payload": cast(dict[str, Any], payload),
                "version": version,
                "batch_id": change_batch_by_key.get((object_id, version)),
                "origin": "current_state",
            }
        object_ids = sorted(samples_by_object)[:sample_limit]
        selected = [samples_by_object[object_id] for object_id in object_ids]
        if object_ids:
            batch_lookup = await session.execute(
                text(
                    """
                    SELECT record.object_id, record.version, record.batch_id
                    FROM platform_raw.raw_change_record AS record
                    JOIN platform_raw.raw_ingest_batch AS batch
                      ON batch.batch_id = record.batch_id
                    WHERE record.source_application_id = :source_application_id
                      AND record.object_type = :object_type
                      AND record.payload_contract_version = :payload_contract_version
                      AND record.operation <> 'delete'
                      AND batch.status = 'loaded'
                      AND COALESCE(batch.purpose, 'production') = 'production'
                      AND record.object_id IN :object_ids
                    """
                ).bindparams(bindparam("object_ids", expanding=True)),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "payload_contract_version": contract_version,
                    "object_ids": object_ids,
                },
            )
            exact_batch: dict[tuple[str, int], str] = {}
            for row in batch_lookup.all():
                if row.batch_id is None:
                    continue
                exact_batch[(str(row.object_id), int(row.version))] = str(row.batch_id)
            for item in selected:
                resolved = exact_batch.get((str(item["object_id"]), int(item["version"])))
                if resolved is not None:
                    item["batch_id"] = resolved
        payloads = [cast(dict[str, Any], item["payload"]) for item in selected]
        schema, warnings = infer_draft_schema(payloads)
        queried_at = datetime.now(UTC)
        evidence = {
            "payload_contract_version": contract_version,
            "sample_limit": sample_limit,
            "coverage": {
                "sample_count": len(payloads),
                "observed_field_count": len(schema.get("properties", {})),
                "current_state_count": len(current_rows),
                "change_record_count": len(change_rows),
            },
            "samples": [
                {
                    "object_id": item["object_id"],
                    "version": item["version"],
                    "batch_id": item["batch_id"],
                    "origin": item["origin"],
                }
                for item in selected
            ],
            "cutoff": {
                "queried_at": queried_at.isoformat(),
                "sample_limit": sample_limit,
                "current_state_max_updated_at": (
                    None
                    if not current_updated_at
                    else max(current_updated_at).isoformat()
                ),
                "change_record_max_version": (
                    None if not change_versions else max(change_versions)
                ),
                "sample_batch_ids": sorted(
                    {
                        str(item["batch_id"])
                        for item in selected
                        if item["batch_id"] is not None
                    }
                ),
            },
            "object_ids_sha256": canonical_json_digest(object_ids),
            "content_sha256": canonical_json_digest(payloads),
            "warnings": warnings,
        }
        return await self.save_draft(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
            json_schema=schema,
            origin="INFERRED_FROM_RAW",
            inference_evidence_ref=json.dumps(evidence, sort_keys=True),
        )

    async def activate(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        contract_version: str,
        reviewed_by: str,
        expected_schema_fingerprint: str,
    ) -> IngestContractRow:
        existing = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
        )
        if existing is None or existing.status != "DRAFT":
            raise IngestContractConflictError(
                "only a DRAFT contract can be activated"
            )
        await lock_ingest_source(session, source_application_id, object_type)
        existing = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
            for_update=True,
        )
        if existing is None or existing.status != "DRAFT":
            raise IngestContractConflictError(
                "only a DRAFT contract can be activated"
            )
        if existing.schema_fingerprint != expected_schema_fingerprint:
            raise IngestContractConflictError(
                "contract draft changed since it was reviewed"
            )
        await _assert_no_active_push_generation(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
        )
        await _assert_activation_certification(session, existing)
        await session.execute(
            text(
                """
                UPDATE platform_core.ingest_contract
                SET status = 'DEPRECATED', updated_at = CURRENT_TIMESTAMP
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND status = 'ACTIVE'
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
            },
        )
        result = await session.execute(
            text(
                """
                UPDATE platform_core.ingest_contract
                SET status = 'ACTIVE',
                    reviewed_by = :reviewed_by,
                    reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND contract_version = :contract_version
                  AND status = 'DRAFT'
                  AND schema_fingerprint = :expected_schema_fingerprint
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "contract_version": contract_version,
                "reviewed_by": reviewed_by,
                "expected_schema_fingerprint": expected_schema_fingerprint,
            },
        )
        if int(getattr(result, "rowcount", 0) or 0) == 0:
            raise IngestContractConflictError(
                "contract draft changed since it was reviewed"
            )
        stored = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
        )
        if stored is None or stored.status != "ACTIVE":  # pragma: no cover
            raise IngestContractError("failed to activate ingest contract")
        return stored

    async def reject(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        contract_version: str,
        reviewed_by: str,
        expected_schema_fingerprint: str,
    ) -> IngestContractRow:
        existing = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
        )
        if existing is None or existing.status != "DRAFT":
            raise IngestContractConflictError("only a DRAFT contract can be rejected")
        await lock_ingest_source(session, source_application_id, object_type)
        existing = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
            for_update=True,
        )
        if existing is None or existing.status != "DRAFT":
            raise IngestContractConflictError("only a DRAFT contract can be rejected")
        if existing.schema_fingerprint != expected_schema_fingerprint:
            raise IngestContractConflictError(
                "contract draft changed since it was reviewed"
            )
        result = await session.execute(
            text(
                """
                UPDATE platform_core.ingest_contract
                SET status = 'REJECTED',
                    reviewed_by = :reviewed_by,
                    reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND contract_version = :contract_version
                  AND status = 'DRAFT'
                  AND schema_fingerprint = :expected_schema_fingerprint
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "contract_version": contract_version,
                "reviewed_by": reviewed_by,
                "expected_schema_fingerprint": expected_schema_fingerprint,
            },
        )
        if int(getattr(result, "rowcount", 0) or 0) == 0:
            raise IngestContractConflictError(
                "contract draft changed since it was reviewed"
            )
        stored = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
        )
        if stored is None:  # pragma: no cover
            raise IngestContractError("failed to reject ingest contract")
        return stored

    async def list_certifications(
        self, session: AsyncSession
    ) -> list[IngestCertificationRow]:
        result = await session.execute(
            text(
                f"""
                SELECT {_CERT_COLUMNS}
                FROM platform_core.ingest_contract_certification
                ORDER BY source_application_id, object_type, created_at
                """
            )
        )
        return [_row_to_certification(row) for row in result.all()]

    async def get_certification(
        self,
        session: AsyncSession,
        certification_id: UUID,
        *,
        for_update: bool = False,
    ) -> IngestCertificationRow | None:
        lock = " FOR UPDATE" if for_update else ""
        result = await session.execute(
            text(
                f"""
                SELECT {_CERT_COLUMNS}
                FROM platform_core.ingest_contract_certification
                WHERE certification_id = :certification_id
                {lock}
                """
            ),
            {"certification_id": certification_id},
        )
        row = result.one_or_none()
        return None if row is None else _row_to_certification(row)

    async def create_certification(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        contract_version: str,
        rows_validated: int = 0,
        full_regression_status: str | None = None,
        incremental_regression_status: str | None = None,
        rollback_drill_status: str | None = None,
        observation_batch_from: UUID | None = None,
        observation_batch_to: UUID | None = None,
        violation_summary: dict[str, Any] | None = None,
        exemption_summary: dict[str, Any] | None = None,
        full_regression_evidence_ref: str | None = None,
        incremental_regression_evidence_ref: str | None = None,
        rollback_drill_evidence_ref: str | None = None,
    ) -> IngestCertificationRow:
        _assert_certification_evidence(
            rows_validated=rows_validated,
            full_regression_status=full_regression_status,
            incremental_regression_status=incremental_regression_status,
            rollback_drill_status=rollback_drill_status,
            observation_batch_from=observation_batch_from,
            observation_batch_to=observation_batch_to,
            violation_summary=violation_summary,
            exemption_summary=exemption_summary,
            full_regression_evidence_ref=full_regression_evidence_ref,
            incremental_regression_evidence_ref=incremental_regression_evidence_ref,
            rollback_drill_evidence_ref=rollback_drill_evidence_ref,
        )
        if observation_batch_from is None or observation_batch_to is None:
            raise IngestContractError(
                "certification requires an observation batch window"
            )
        await lock_ingest_source(session, source_application_id, object_type)
        contract = await self.get_contract(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            contract_version=contract_version,
            for_update=True,
        )
        if contract is None or contract.status not in {"ACTIVE", "DRAFT"}:
            raise IngestContractConflictError(
                "certification requires an ACTIVE or DRAFT ingest contract"
            )
        live_fingerprint = schema_fingerprint(contract.json_schema)
        if live_fingerprint != contract.schema_fingerprint:
            raise IngestContractConflictError(
                "certification schema_fingerprint does not match the current "
                "contract"
            )
        transport_mode = await _registered_transport_mode(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
        )
        bound = await bind_certification_evidence(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
            schema_fingerprint=contract.schema_fingerprint,
            json_schema=contract.json_schema,
            rows_validated=rows_validated,
            observation_batch_from=observation_batch_from,
            observation_batch_to=observation_batch_to,
            exemption_summary=exemption_summary or {},
            full_regression_evidence_ref=full_regression_evidence_ref or "",
            incremental_regression_evidence_ref=incremental_regression_evidence_ref
            or "",
            rollback_drill_evidence_ref=rollback_drill_evidence_ref or "",
            transport_mode=transport_mode,
        )
        result = await session.execute(
            text(
                f"""
                INSERT INTO platform_core.ingest_contract_certification (
                    source_application_id, object_type, contract_version,
                    schema_fingerprint, rows_validated, full_regression_status,
                    incremental_regression_status, rollback_drill_status, status,
                    observation_batch_from, observation_batch_to,
                    violation_summary, exemption_summary,
                    full_regression_evidence_ref,
                    incremental_regression_evidence_ref,
                    rollback_drill_evidence_ref,
                    transport_mode
                ) VALUES (
                    :source_application_id, :object_type, :contract_version,
                    :schema_fingerprint, :rows_validated, :full_regression_status,
                    :incremental_regression_status, :rollback_drill_status, 'DRAFT',
                    :observation_batch_from, :observation_batch_to,
                    CAST(:violation_summary AS jsonb),
                    CAST(:exemption_summary AS jsonb),
                    :full_regression_evidence_ref,
                    :incremental_regression_evidence_ref,
                    :rollback_drill_evidence_ref,
                    :transport_mode
                )
                RETURNING {_CERT_COLUMNS}
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "contract_version": contract_version,
                "schema_fingerprint": contract.schema_fingerprint,
                "rows_validated": bound.rows_validated,
                "full_regression_status": full_regression_status,
                "incremental_regression_status": incremental_regression_status,
                "rollback_drill_status": rollback_drill_status,
                "observation_batch_from": observation_batch_from,
                "observation_batch_to": observation_batch_to,
                "violation_summary": json.dumps(
                    bound.violation_summary, sort_keys=True
                ),
                "exemption_summary": json.dumps(exemption_summary or {}, sort_keys=True),
                "full_regression_evidence_ref": full_regression_evidence_ref,
                "incremental_regression_evidence_ref": incremental_regression_evidence_ref,
                "rollback_drill_evidence_ref": rollback_drill_evidence_ref,
                "transport_mode": transport_mode,
            },
        )
        row = result.one()
        return _row_to_certification(row)

    async def approve_certification(
        self,
        session: AsyncSession,
        *,
        certification_id: UUID,
        role: ApproveRole,
        actor: str,
    ) -> IngestCertificationRow:
        existing = await self.get_certification(
            session, certification_id, for_update=True
        )
        if existing is None:
            raise IngestContractConflictError("certification does not exist")
        if existing.status != "DRAFT":
            raise IngestContractConflictError(
                "only a DRAFT certification can be approved"
            )
        _assert_certification_evidence(
            rows_validated=existing.rows_validated,
            full_regression_status=existing.full_regression_status,
            incremental_regression_status=existing.incremental_regression_status,
            rollback_drill_status=existing.rollback_drill_status,
            observation_batch_from=existing.observation_batch_from,
            observation_batch_to=existing.observation_batch_to,
            violation_summary=existing.violation_summary,
            exemption_summary=existing.exemption_summary,
            full_regression_evidence_ref=existing.full_regression_evidence_ref,
            incremental_regression_evidence_ref=existing.incremental_regression_evidence_ref,
            rollback_drill_evidence_ref=existing.rollback_drill_evidence_ref,
        )
        if role == "data_owner":
            if existing.data_owner_approved_by is not None:
                raise IngestContractConflictError("data owner already approved")
            if existing.operator_approved_by == actor:
                raise IngestContractConflictError(
                    "data owner and operator approvals must be distinct people"
                )
            result = await session.execute(
                text(
                    """
                    UPDATE platform_core.ingest_contract_certification
                    SET data_owner_approved_by = :actor,
                        data_owner_approved_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE certification_id = :certification_id
                      AND status = 'DRAFT'
                      AND data_owner_approved_by IS NULL
                      AND (
                            operator_approved_by IS NULL
                            OR operator_approved_by <> :actor
                      )
                    """
                ),
                {"actor": actor, "certification_id": certification_id},
            )
            if int(getattr(result, "rowcount", 0) or 0) == 0:
                raise IngestContractConflictError(
                    "data owner and operator approvals must be distinct people"
                )
        elif role == "operator":
            if existing.operator_approved_by is not None:
                raise IngestContractConflictError("operator already approved")
            if existing.data_owner_approved_by == actor:
                raise IngestContractConflictError(
                    "data owner and operator approvals must be distinct people"
                )
            result = await session.execute(
                text(
                    """
                    UPDATE platform_core.ingest_contract_certification
                    SET operator_approved_by = :actor,
                        operator_approved_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE certification_id = :certification_id
                      AND status = 'DRAFT'
                      AND operator_approved_by IS NULL
                      AND (
                            data_owner_approved_by IS NULL
                            OR data_owner_approved_by <> :actor
                      )
                    """
                ),
                {"actor": actor, "certification_id": certification_id},
            )
            if int(getattr(result, "rowcount", 0) or 0) == 0:
                raise IngestContractConflictError(
                    "data owner and operator approvals must be distinct people"
                )
        else:
            raise IngestContractError("unsupported approval role")
        refreshed = await self.get_certification(session, certification_id)
        if refreshed is None:  # pragma: no cover
            raise IngestContractError("failed to load certification")
        if (
            refreshed.data_owner_approved_by is not None
            and refreshed.operator_approved_by is not None
        ):
            if refreshed.data_owner_approved_by == refreshed.operator_approved_by:
                raise IngestContractConflictError(
                    "data owner and operator approvals must be distinct people"
                )
            await lock_ingest_source(
                session,
                refreshed.source_application_id,
                refreshed.object_type,
            )
            contract = await self.get_contract(
                session,
                source_application_id=refreshed.source_application_id,
                object_type=refreshed.object_type,
                contract_version=refreshed.contract_version,
                for_update=True,
            )
            if contract is None:
                raise IngestContractConflictError(
                    "certification contract no longer exists"
                )
            if contract.status not in {"DRAFT", "ACTIVE"}:
                raise IngestContractConflictError(
                    "certification contract is no longer approvable"
                )
            live_fingerprint = schema_fingerprint(contract.json_schema)
            if (
                contract.schema_fingerprint != refreshed.schema_fingerprint
                or live_fingerprint != refreshed.schema_fingerprint
            ):
                raise IngestContractConflictError(
                    "certification schema_fingerprint does not match the current "
                    "contract"
                )
            current_mode = await _registered_transport_mode(
                session,
                source_application_id=refreshed.source_application_id,
                object_type=refreshed.object_type,
            )
            if refreshed.transport_mode != current_mode:
                raise IngestContractConflictError(
                    "certification transport_mode does not match the current source"
                )
            if (
                refreshed.observation_batch_from is None
                or refreshed.observation_batch_to is None
            ):
                raise IngestContractError(
                    "certification requires an observation batch window"
                )
            await bind_certification_evidence(
                session,
                source_application_id=refreshed.source_application_id,
                object_type=refreshed.object_type,
                schema_fingerprint=contract.schema_fingerprint,
                json_schema=contract.json_schema,
                rows_validated=refreshed.rows_validated,
                observation_batch_from=refreshed.observation_batch_from,
                observation_batch_to=refreshed.observation_batch_to,
                exemption_summary=refreshed.exemption_summary,
                full_regression_evidence_ref=refreshed.full_regression_evidence_ref
                or "",
                incremental_regression_evidence_ref=(
                    refreshed.incremental_regression_evidence_ref or ""
                ),
                rollback_drill_evidence_ref=refreshed.rollback_drill_evidence_ref or "",
                transport_mode=current_mode,
            )
            await session.execute(
                text(
                    """
                    UPDATE platform_core.ingest_contract_certification
                    SET status = 'APPROVED', updated_at = CURRENT_TIMESTAMP
                    WHERE certification_id = :certification_id
                      AND status = 'DRAFT'
                      AND data_owner_approved_by IS NOT NULL
                      AND operator_approved_by IS NOT NULL
                      AND data_owner_approved_by <> operator_approved_by
                    """
                ),
                {"certification_id": certification_id},
            )
            refreshed = await self.get_certification(session, certification_id)
            if refreshed is None:  # pragma: no cover
                raise IngestContractError("failed to approve certification")
        return refreshed
