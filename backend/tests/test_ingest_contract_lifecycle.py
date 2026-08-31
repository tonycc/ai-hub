"""Portal ingest contract lifecycle (design §4.2)."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from ai_hub_platform.modules.ingest.contract import schema_fingerprint
from ai_hub_platform.modules.ingest.contract_store import (
    OBSERVATION_REPLAY_MAX_ROWS,
    IngestCertificationRow,
    IngestContractConflictError,
    IngestContractError,
    IngestContractRow,
    IngestContractStore,
    bind_certification_evidence,
)
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig


def _certification_row(**overrides: object) -> IngestCertificationRow:
    values: dict[str, object] = {
        "certification_id": uuid4(),
        "source_application_id": "e10-adapter",
        "object_type": "erp.item",
        "contract_version": "item.v1",
        "schema_fingerprint": "a" * 64,
        "rows_validated": 10,
        "full_regression_status": "passed",
        "incremental_regression_status": "passed",
        "rollback_drill_status": "passed",
        "data_owner_approved_by": None,
        "data_owner_approved_at": None,
        "operator_approved_by": None,
        "operator_approved_at": None,
        "status": "DRAFT",
        "updated_at": datetime.now(UTC),
        "observation_batch_from": uuid4(),
        "observation_batch_to": uuid4(),
        "violation_summary": {"unexempted": []},
        "exemption_summary": {"items": []},
        "full_regression_evidence_ref": "full-run-1",
        "incremental_regression_evidence_ref": "inc-run-1",
        "rollback_drill_evidence_ref": "rollback-drill-1",
    }
    values.update(overrides)
    return IngestCertificationRow(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_save_draft_rejects_invalid_json_schema() -> None:
    with pytest.raises(IngestContractError, match="valid JSON Schema"):
        await IngestContractStore().save_draft(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema={"type": "object", "properties": []},
        )


@pytest.mark.asyncio
async def test_activate_rejects_non_draft_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    existing = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema={"type": "object"},
        schema_fingerprint="a" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="ACTIVE",
        reviewed_by="owner",
        reviewed_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return existing

    monkeypatch.setattr(store, "get_contract", fake_get)
    with pytest.raises(IngestContractConflictError, match="DRAFT"):
        await store.activate(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            reviewed_by="owner",
            expected_schema_fingerprint="a" * 64,
        )


@pytest.mark.asyncio
async def test_certification_approvals_must_be_distinct_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    existing = _certification_row(data_owner_approved_by="alice")

    async def fake_get(*args: object, **kwargs: object) -> IngestCertificationRow:
        del args, kwargs
        return existing

    monkeypatch.setattr(store, "get_certification", fake_get)
    with pytest.raises(IngestContractConflictError, match="distinct people"):
        await store.approve_certification(
            object(),  # type: ignore[arg-type]
            certification_id=existing.certification_id,
            role="operator",
            actor="alice",
        )


@pytest.mark.asyncio
async def test_save_draft_rejects_non_object_json_schema() -> None:
    with pytest.raises(IngestContractError, match="type must be object"):
        await IngestContractStore().save_draft(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema={"type": "string"},
        )


@pytest.mark.asyncio
async def test_create_certification_rejects_empty_evidence() -> None:
    store = IngestContractStore()
    contract = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema={"type": "object"},
        schema_fingerprint="a" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="ACTIVE",
        reviewed_by="owner",
        reviewed_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return contract

    store.get_contract = fake_get  # type: ignore[method-assign]
    with pytest.raises(IngestContractError, match="rows_validated"):
        await store.create_certification(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            rows_validated=0,
            full_regression_status="passed",
            incremental_regression_status="passed",
            rollback_drill_status="passed",
        )


@pytest.mark.asyncio
async def test_approve_certification_rejects_missing_regression_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    existing = _certification_row(full_regression_status=None)

    async def fake_get(*args: object, **kwargs: object) -> IngestCertificationRow:
        del args, kwargs
        return existing

    monkeypatch.setattr(store, "get_certification", fake_get)
    with pytest.raises(IngestContractError, match="full_regression_status"):
        await store.approve_certification(
            object(),  # type: ignore[arg-type]
            certification_id=existing.certification_id,
            role="data_owner",
            actor="alice",
        )


@pytest.mark.asyncio
async def test_activate_rejects_in_flight_push_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    existing = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v2",
        json_schema={"type": "object"},
        schema_fingerprint="b" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="DRAFT",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return existing

    async def fake_lock(*args: object, **kwargs: object) -> None:
        del args, kwargs

    class _Result:
        def one_or_none(self) -> object:
            return object()

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Result:
            del args, kwargs
            return _Result()

    monkeypatch.setattr(store, "get_contract", fake_get)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.lock_ingest_source",
        fake_lock,
    )
    with pytest.raises(IngestContractConflictError, match="push generation"):
        await store.activate(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v2",
            reviewed_by="owner",
            expected_schema_fingerprint="b" * 64,
        )


def test_approval_update_keeps_distinct_actor_in_sql() -> None:
    from ai_hub_platform.modules.ingest.contract_store import IngestContractStore

    source = inspect.getsource(IngestContractStore.approve_certification)
    assert "operator_approved_by <> :actor" in source
    assert "data_owner_approved_by <> :actor" in source
    assert "bind_certification_evidence" in source
    assert "lock_ingest_source" in source
    assert "for_update=True" in source
    assert "schema_fingerprint(contract.json_schema)" in source
    assert source.index("lock_ingest_source") < source.index(
        "bind_certification_evidence"
    )
    assert "FOR UPDATE" in inspect.getsource(IngestContractStore.get_certification)


def test_portal_exposes_contract_lifecycle_routes() -> None:
    from ai_hub_platform.api.ingest_contracts import router

    paths = {route.path for route in router.routes}  # type: ignore[attr-defined]
    assert "/portal-api/v1/ingest/contracts" in paths
    assert "/portal-api/v1/ingest/contracts/activate" in paths
    assert "/portal-api/v1/ingest/contracts/reject" in paths
    assert "/portal-api/v1/ingest/contracts/certifications" in paths
    assert (
        "/portal-api/v1/ingest/contracts/certifications/{certification_id}/approve"
        in paths
    )


@pytest.mark.asyncio
async def test_create_certification_rejects_self_reported_passed_without_window() -> None:
    store = IngestContractStore()
    contract = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema={"type": "object"},
        schema_fingerprint="a" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="ACTIVE",
        reviewed_by="owner",
        reviewed_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return contract

    store.get_contract = fake_get  # type: ignore[method-assign]
    with pytest.raises(IngestContractError, match="observation batch window"):
        await store.create_certification(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            rows_validated=1,
            full_regression_status="passed",
            incremental_regression_status="passed",
            rollback_drill_status="passed",
        )


@pytest.mark.asyncio
async def test_create_certification_rejects_unexempted_violations() -> None:
    store = IngestContractStore()
    contract = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema={"type": "object"},
        schema_fingerprint="a" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="DRAFT",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return contract

    store.get_contract = fake_get  # type: ignore[method-assign]
    with pytest.raises(IngestContractError, match="unexempted"):
        await store.create_certification(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            rows_validated=12,
            observation_batch_from=uuid4(),
            observation_batch_to=uuid4(),
            violation_summary={"unexempted": [{"code": "unknown_field"}]},
            exemption_summary={"items": []},
            full_regression_status="passed",
            incremental_regression_status="passed",
            rollback_drill_status="passed",
            full_regression_evidence_ref="full-run-1",
            incremental_regression_evidence_ref="inc-run-1",
            rollback_drill_evidence_ref="rollback-drill-1",
        )


@pytest.mark.asyncio
async def test_activate_rejects_stale_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    existing = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v2",
        json_schema={"type": "object"},
        schema_fingerprint="b" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="DRAFT",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return existing

    async def fake_lock(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(store, "get_contract", fake_get)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.lock_ingest_source",
        fake_lock,
    )
    with pytest.raises(IngestContractConflictError, match="changed since"):
        await store.activate(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v2",
            reviewed_by="owner",
            expected_schema_fingerprint="a" * 64,
        )


@pytest.mark.asyncio
async def test_activate_requires_cert_when_source_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    existing = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v2",
        json_schema={"type": "object"},
        schema_fingerprint="b" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="DRAFT",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return existing

    async def fake_lock(*args: object, **kwargs: object) -> None:
        del args, kwargs

    class _Empty:
        def one_or_none(self) -> None:
            return None

    captured: dict[str, object] = {}

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Empty:
            captured["sql"] = str(statement)
            captured["params"] = params
            return _Empty()

    async def fake_source(*args: object, **kwargs: object) -> object:
        del args, kwargs

        class _Row:
            config = IngestSourceConfig(
                source_application_id="e10-adapter",
                object_type="erp.item",
                transport_mode="PUSH_AGENT",
                enabled=True,
                push_protocol_version="1",
                contract_validation_mode="ENFORCE",
            )

        return _Row()

    monkeypatch.setattr(store, "get_contract", fake_get)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.lock_ingest_source",
        fake_lock,
    )
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.IngestConfigStore.get_source",
        fake_source,
    )
    with pytest.raises(IngestContractConflictError, match="APPROVED"):
        await store.activate(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v2",
            reviewed_by="owner",
            expected_schema_fingerprint="b" * 64,
        )
    assert "transport_mode = :transport_mode" in str(captured["sql"])
    assert isinstance(captured["params"], dict)
    assert captured["params"]["transport_mode"] == "PUSH_AGENT"


def test_activate_sql_binds_reviewed_fingerprint() -> None:
    import inspect

    from ai_hub_platform.modules.ingest import contract_store as store_mod

    source = inspect.getsource(IngestContractStore.activate)
    assert "schema_fingerprint = :expected_schema_fingerprint" in source
    assert "_assert_activation_certification" in source
    assert "except AttributeError" not in inspect.getsource(
        store_mod._assert_activation_certification  # pyright: ignore[reportPrivateUsage]
    )


def test_approval_role_is_derived_from_server_permissions() -> None:
    from ai_hub_platform.api import ingest_contracts as api

    assert "as_role" not in api.CertificationApproveRequest.model_fields
    assert api.INGEST_CERTIFY_DATA_OWNER == "platform.ingest.certify.data_owner"
    assert api.INGEST_CERTIFY_OPERATOR == "platform.ingest.certify.operator"
    source = inspect.getsource(api.approve_ingest_certification)
    assert "_approval_role" in source
    assert "body.as_role" not in source
    approve = inspect.getsource(api.approve_ingest_certification)
    assert "portal_any_permission_dependency" in approve
    assert "INGEST_WRITE" not in approve
    assert "INGEST_CERTIFY_DATA_OWNER" in approve
    assert "INGEST_CERTIFY_OPERATOR" in approve


def test_contract_identifier_models_match_database_bounds() -> None:
    from ai_hub_platform.api.ingest_contracts import (
        ContractDraftRequest,
        ContractVersionRequest,
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ContractDraftRequest(
            source_application_id="   ",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema={"type": "object"},
        )
    with pytest.raises(ValidationError):
        ContractVersionRequest(
            source_application_id="e10-adapter",
            object_type="x" * 121,
            contract_version="item.v1",
            expected_schema_fingerprint="a" * 64,
        )
    with pytest.raises(ValidationError):
        ContractVersionRequest(
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="v" * 101,
            expected_schema_fingerprint="a" * 64,
        )


def test_certification_rows_validated_fits_integer_column() -> None:
    from ai_hub_platform.api.ingest_contracts import CertificationCreateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CertificationCreateRequest(
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            rows_validated=2_147_483_648,
            observation_batch_from=uuid4(),
            observation_batch_to=uuid4(),
            violation_summary={"unexempted": []},
            exemption_summary={"items": []},
            full_regression_status="passed",
            incremental_regression_status="passed",
            rollback_drill_status="passed",
            full_regression_evidence_ref=str(uuid4()),
            incremental_regression_evidence_ref=str(uuid4()),
            rollback_drill_evidence_ref=str(uuid4()),
        )


@pytest.mark.asyncio
async def test_create_certification_rejects_unbound_observation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.modules.ingest.contract_store import bind_certification_evidence

    store = IngestContractStore()
    contract = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema={"type": "object"},
        schema_fingerprint=schema_fingerprint({"type": "object"}),
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="DRAFT",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=datetime.now(UTC),
    )

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return contract

    class _Empty:
        def all(self) -> list[object]:
            return []

        def scalar_one(self) -> int:
            return 0

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Empty:
            return _Empty()

    async def fake_lock(*args: object, **kwargs: object) -> None:
        del args, kwargs

    async def fake_mode(*args: object, **kwargs: object) -> str:
        del args, kwargs
        return "PULL_EXPORT"

    monkeypatch.setattr(store, "get_contract", fake_get)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.lock_ingest_source",
        fake_lock,
    )
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store._registered_transport_mode",
        fake_mode,
    )
    refs = (str(uuid4()), str(uuid4()), str(uuid4()))
    with pytest.raises(IngestContractError, match="loaded Raw ingest batches"):
        await store.create_certification(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            rows_validated=1,
            observation_batch_from=uuid4(),
            observation_batch_to=uuid4(),
            violation_summary={"unexempted": []},
            exemption_summary={"items": []},
            full_regression_status="passed",
            incremental_regression_status="passed",
            rollback_drill_status="passed",
            full_regression_evidence_ref=refs[0],
            incremental_regression_evidence_ref=refs[1],
            rollback_drill_evidence_ref=refs[2],
        )
    bind = inspect.getsource(bind_certification_evidence)
    assert "raw_ingest_batch" in bind
    assert "conformance_run" in bind
    assert "conformance_check" in bind
    assert "DATA_INGEST" in bind
    assert "schema_fingerprint IS NOT NULL" in bind
    assert "replay_payloads_against_schema" in bind
    assert "certification_kind" in bind
    assert "completed_at" in bind
    assert "finished_at" in bind
    assert "transport_mode" in bind
    assert "unpack_data_ingest_check_evidence" in bind
    assert "SELECT COUNT(*)" not in bind
    assert "actual_rows = len(records)" in bind
    assert "LIMIT :record_limit" in bind
    from ai_hub_platform.modules.ingest import contract_store as store_mod

    assert "expires_at" in inspect.getsource(store_mod)


def test_infer_draft_writes_inference_evidence_and_origin() -> None:
    from ai_hub_platform.api import ingest_contracts as api

    save = inspect.getsource(IngestContractStore.save_draft)
    infer = inspect.getsource(IngestContractStore.infer_draft_from_raw)
    route = inspect.getsource(api.infer_ingest_contract_draft)
    assert "inference_evidence_ref" in save
    assert "infer_draft_schema" in infer
    assert "INFERRED_FROM_RAW" in infer
    assert "raw_current_state" in infer
    assert "payload_contract_version" in infer
    assert "object_ids_sha256" in infer
    assert "content_sha256" in infer
    assert "samples" in infer
    assert "cutoff" in infer
    assert "sample_batch_ids" in infer
    assert "IN :object_ids" in infer
    assert "DISTINCT ON (record.object_id)" in infer
    assert "COALESCE(batch.purpose, 'production') = 'production'" in infer
    assert "if object_id in samples_by_object" in infer
    from ai_hub_platform.modules.ingest import contract_store as store_mod

    assert "assert_closed_json_schema" in inspect.getsource(store_mod)
    assert "unpack_data_ingest_check_evidence" in inspect.getsource(store_mod)
    assert "infer_draft_from_raw" in route


def test_ingest_view_shows_and_confirms_certification_evidence() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "src/views/IngestView.vue"
    ).read_text(encoding="utf-8")
    assert "ElMessageBox.confirm" in source
    assert "observation_batch_from" in source
    assert "full_regression_evidence_ref" in source
    assert "schema_fingerprint" in source
    assert "从 Raw 推导草稿" in source
    assert "ingestInferContract" in source
    assert "canCertify" in source
    assert "确认激活契约" in source
    assert "将替换当前 ACTIVE 版本" in source
    assert "isPushSource" in source
    assert "从未推送" in source
    assert "violation_summary" in source
    assert "观察违规" in source
    assert "summarizeCertificationIssues" in source
    assert "broadExemptionWarning" in source
    assert "lastStatusOk" in source
    assert "lastStatusFailed" in source
    assert "last_success_at" in source
    assert "padding: var(--space-card-lg)" in source
    assert "padding: 18px 22px 8px" not in source


class _BatchRow:
    def __init__(
        self,
        batch_id: UUID,
        *,
        fingerprint: str | None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        transport_mode: str = "PULL_EXPORT",
    ) -> None:
        self.batch_id = batch_id
        self.source_application_id = "e10-adapter"
        self.object_type = "erp.item"
        self.status = "loaded"
        self.schema_fingerprint = fingerprint
        self.started_at = started_at or datetime.now(UTC)
        self.finished_at = finished_at if finished_at is not None else self.started_at
        self.transport_mode = transport_mode


class _ChangeRow:
    def __init__(self, payload: dict[str, object]) -> None:
        self.object_id = "I-1"
        self.operation = "upsert"
        self.version = 1
        self.payload = payload


class _Rows:
    def __init__(self, rows: list[object], scalar: int = 0) -> None:
        self._rows = rows
        self._scalar = scalar

    def all(self) -> list[object]:
        return self._rows

    def scalar_one(self) -> int:
        return self._scalar


@pytest.mark.asyncio
async def test_bind_certification_rejects_null_batch_fingerprint() -> None:
    batch_from = uuid4()
    batch_to = uuid4()

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Rows:
            return _Rows(
                [
                    _BatchRow(batch_from, fingerprint=None),
                    _BatchRow(batch_to, fingerprint=None),
                ]
            )

    with pytest.raises(IngestContractError, match="schema_fingerprint"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint="a" * 64,
            json_schema={"type": "object"},
            rows_validated=1,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(uuid4()),
            incremental_regression_evidence_ref=str(uuid4()),
            rollback_drill_evidence_ref=str(uuid4()),
            transport_mode="PULL_EXPORT",
        )


@pytest.mark.asyncio
async def test_bind_certification_replays_schema_and_rejects_unexempted() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            del params
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(batch_from, fingerprint=fingerprint),
                        _BatchRow(batch_to, fingerprint=fingerprint),
                    ]
                )
            if "record.object_id" in sql:
                return _Rows([_ChangeRow({})])
            raise AssertionError(sql)

    with pytest.raises(IngestContractError, match="unexempted"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
            rows_validated=1,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(uuid4()),
            incremental_regression_evidence_ref=str(uuid4()),
            rollback_drill_evidence_ref=str(uuid4()),
            transport_mode="PULL_EXPORT",
        )


@pytest.mark.asyncio
async def test_bind_certification_rejects_count_select_drift() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            del params
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(batch_from, fingerprint=fingerprint),
                        _BatchRow(batch_to, fingerprint=fingerprint),
                    ]
                )
            if "COUNT(*)" in sql:
                return _Rows([], scalar=2)
            if "record.object_id" in sql:
                return _Rows([_ChangeRow({"name": "ok"})])
            raise AssertionError(sql)

    with pytest.raises(IngestContractError, match="rows_validated"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={"type": "object"},
            rows_validated=2,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(uuid4()),
            incremental_regression_evidence_ref=str(uuid4()),
            rollback_drill_evidence_ref=str(uuid4()),
            transport_mode="PULL_EXPORT",
        )


@pytest.mark.asyncio
async def test_bind_certification_bounds_observation_rows_before_replay() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(batch_from, fingerprint=fingerprint),
                        _BatchRow(batch_to, fingerprint=fingerprint),
                    ]
                )
            if "record.object_id" in sql:
                assert "LIMIT :record_limit" in sql
                assert isinstance(params, dict)
                assert params["record_limit"] == OBSERVATION_REPLAY_MAX_ROWS + 1
                row = _ChangeRow({"name": "ok"})
                return _Rows([row] * (OBSERVATION_REPLAY_MAX_ROWS + 1))
            raise AssertionError("oversized windows must fail before evidence lookup")

    with pytest.raises(IngestContractError, match="too large to replay"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={"type": "object"},
            rows_validated=OBSERVATION_REPLAY_MAX_ROWS + 2,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(uuid4()),
            incremental_regression_evidence_ref=str(uuid4()),
            rollback_drill_evidence_ref=str(uuid4()),
            transport_mode="PULL_EXPORT",
        )


@pytest.mark.asyncio
async def test_bind_certification_requires_data_ingest_profile_and_kind() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64
    run_ids = (uuid4(), uuid4(), uuid4())

    class _Check:
        def __init__(self, run_id: UUID) -> None:
            self.run_id = run_id
            self.application_id = "e10-adapter"
            self.run_status = "PASSED"
            self.requested_profiles = ["API_ONLY"]
            self.profile = "DATA_INGEST"
            self.check_status = "PASSED"
            self.evidence = {
                "object_type": "erp.item",
                "schema_fingerprint": fingerprint,
                "certification_kind": "full_regression",
            }

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            del params
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(batch_from, fingerprint=fingerprint),
                        _BatchRow(batch_to, fingerprint=fingerprint),
                    ]
                )
            if "record.object_id" in sql:
                return _Rows([_ChangeRow({"name": "ok"})])
            if "conformance_run" in sql:
                return _Rows([_Check(run_id) for run_id in run_ids])
            raise AssertionError(sql)

    with pytest.raises(IngestContractError, match="DATA_INGEST"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={"type": "object"},
            rows_validated=1,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(run_ids[0]),
            incremental_regression_evidence_ref=str(run_ids[1]),
            rollback_drill_evidence_ref=str(run_ids[2]),
            transport_mode="PULL_EXPORT",
        )


@pytest.mark.asyncio
async def test_infer_draft_from_raw_freezes_versioned_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    captured: dict[str, object] = {}
    batch_id = uuid4()
    current_batch_id = uuid4()

    class _Sample:
        def __init__(
            self,
            object_id: str,
            payload: dict[str, object],
            *,
            batch_id: UUID | None = None,
            version: int = 1,
        ) -> None:
            self.object_id = object_id
            self.payload = payload
            self.batch_id = batch_id
            self.version = version
            self.updated_at = datetime.now(UTC)

    class _Result:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def all(self) -> list[object]:
            return self._rows

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Result:
            sql = str(statement)
            assert isinstance(params, dict)
            assert params["payload_contract_version"] == "item.v1"
            if "raw_current_state" in sql:
                assert "payload_contract_version" in sql
                return _Result([_Sample("I-1", {"name": "a"})])
            if "object_ids" in params:
                assert "I-1" in params["object_ids"]
                return _Result(
                    [_Sample("I-1", {"name": "a"}, batch_id=current_batch_id)]
                )
            if "raw_change_record" in sql:
                assert "payload_contract_version" in sql
                assert "loaded" in sql
                return _Result([_Sample("I-2", {"name": None}, batch_id=batch_id)])
            raise AssertionError(sql)

    async def fake_save(*args: object, **kwargs: object) -> IngestContractRow:
        del args
        captured.update(kwargs)
        return IngestContractRow(
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema={"type": "object"},
            schema_fingerprint="a" * 64,
            field_classifications={},
            compatibility_mode="BACKWARD",
            origin="INFERRED_FROM_RAW",
            status="DRAFT",
            reviewed_by=None,
            reviewed_at=None,
            updated_at=datetime.now(UTC),
        )

    monkeypatch.setattr(store, "save_draft", fake_save)
    await store.infer_draft_from_raw(
        _Session(),  # type: ignore[arg-type]
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
    )
    evidence = json.loads(str(captured["inference_evidence_ref"]))
    assert evidence["payload_contract_version"] == "item.v1"
    assert evidence["coverage"]["sample_count"] == 2
    assert evidence["samples"] == [
        {
            "object_id": "I-1",
            "version": 1,
            "batch_id": str(current_batch_id),
            "origin": "current_state",
        },
        {
            "object_id": "I-2",
            "version": 1,
            "batch_id": str(batch_id),
            "origin": "change_record",
        },
    ]
    assert evidence["cutoff"]["sample_limit"] == 200
    assert evidence["cutoff"]["change_record_max_version"] == 1
    assert str(current_batch_id) in evidence["cutoff"]["sample_batch_ids"]
    assert len(evidence["object_ids_sha256"]) == 64
    assert len(evidence["content_sha256"]) == 64


def _runtime_check_evidence(
    *,
    object_type: str,
    fingerprint: str,
    kind: str,
    now: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    verified = now or datetime.now(UTC)
    return {
        "capability_enabled": True,
        "runtime_evidence_present": True,
        "source": "unit-test",
        "verified_at": verified.isoformat(),
        "expires_at": (expires_at or (verified + timedelta(days=1))).isoformat(),
        "runtime": {
            "object_type": object_type,
            "schema_fingerprint": fingerprint,
            "certification_kind": kind,
        },
    }


class _RuntimeCheck:
    def __init__(
        self,
        run_id: UUID,
        *,
        kind: str,
        fingerprint: str,
        now: datetime | None = None,
        expires_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        verified = now or datetime.now(UTC)
        self.run_id = run_id
        self.application_id = "e10-adapter"
        self.run_status = "PASSED"
        self.requested_profiles = ["DATA_INGEST"]
        self.profile = "DATA_INGEST"
        self.check_status = "PASSED"
        self.completed_at = completed_at or verified
        self.evidence = _runtime_check_evidence(
            object_type="erp.item",
            fingerprint=fingerprint,
            kind=kind,
            now=verified,
            expires_at=expires_at,
        )


@pytest.mark.asyncio
async def test_bind_certification_accepts_official_runtime_evidence_shape() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64
    now = datetime.now(UTC)
    run_ids = (uuid4(), uuid4(), uuid4())
    kinds = ("full_regression", "incremental_regression", "rollback_drill")
    checks = [
        _RuntimeCheck(run_id, kind=kind, fingerprint=fingerprint, now=now)
        for run_id, kind in zip(run_ids, kinds, strict=True)
    ]

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            del params
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(batch_from, fingerprint=fingerprint, started_at=now),
                        _BatchRow(batch_to, fingerprint=fingerprint, started_at=now),
                    ]
                )
            if "record.object_id" in sql:
                return _Rows([_ChangeRow({"name": "ok"})])
            if "conformance_run" in sql:
                return _Rows(list(checks))
            raise AssertionError(sql)

    bound = await bind_certification_evidence(
        _Session(),  # type: ignore[arg-type]
        source_application_id="e10-adapter",
        object_type="erp.item",
        schema_fingerprint=fingerprint,
        json_schema={"type": "object"},
        rows_validated=1,
        observation_batch_from=batch_from,
        observation_batch_to=batch_to,
        exemption_summary={"items": []},
        full_regression_evidence_ref=str(run_ids[0]),
        incremental_regression_evidence_ref=str(run_ids[1]),
        rollback_drill_evidence_ref=str(run_ids[2]),
        transport_mode="PULL_EXPORT",
    )
    assert bound.rows_validated == 1
    assert bound.violation_summary["unexempted"] == []


@pytest.mark.asyncio
async def test_bind_certification_rejects_expired_or_stale_runtime_evidence() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64
    window = datetime.now(UTC)
    run_ids = (uuid4(), uuid4(), uuid4())
    kinds = ("full_regression", "incremental_regression", "rollback_drill")
    stale = window - timedelta(days=2)
    checks = [
        _RuntimeCheck(
            run_id,
            kind=kind,
            fingerprint=fingerprint,
            now=stale,
            expires_at=stale + timedelta(hours=1),
            completed_at=stale,
        )
        for run_id, kind in zip(run_ids, kinds, strict=True)
    ]

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            del params
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(batch_from, fingerprint=fingerprint, started_at=window),
                        _BatchRow(batch_to, fingerprint=fingerprint, started_at=window),
                    ]
                )
            if "record.object_id" in sql:
                return _Rows([_ChangeRow({"name": "ok"})])
            if "conformance_run" in sql:
                return _Rows(list(checks))
            raise AssertionError(sql)

    with pytest.raises(IngestContractError, match="expired"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={"type": "object"},
            rows_validated=1,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(run_ids[0]),
            incremental_regression_evidence_ref=str(run_ids[1]),
            rollback_drill_evidence_ref=str(run_ids[2]),
            transport_mode="PULL_EXPORT",
        )


@pytest.mark.asyncio
async def test_bind_certification_rejects_verified_at_before_window_end() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64
    window_start = datetime.now(UTC) - timedelta(hours=2)
    window_end = datetime.now(UTC)
    run_ids = (uuid4(), uuid4(), uuid4())
    kinds = ("full_regression", "incremental_regression", "rollback_drill")
    checks = [
        _RuntimeCheck(
            run_id,
            kind=kind,
            fingerprint=fingerprint,
            now=window_start,
            expires_at=window_end + timedelta(days=1),
            completed_at=window_end + timedelta(minutes=5),
        )
        for run_id, kind in zip(run_ids, kinds, strict=True)
    ]

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            del params
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(
                            batch_from, fingerprint=fingerprint, started_at=window_start
                        ),
                        _BatchRow(
                            batch_to, fingerprint=fingerprint, started_at=window_end
                        ),
                    ]
                )
            if "record.object_id" in sql:
                return _Rows([_ChangeRow({"name": "ok"})])
            if "conformance_run" in sql:
                return _Rows(list(checks))
            raise AssertionError(sql)

    with pytest.raises(IngestContractError, match="observation window"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={"type": "object"},
            rows_validated=1,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(run_ids[0]),
            incremental_regression_evidence_ref=str(run_ids[1]),
            rollback_drill_evidence_ref=str(run_ids[2]),
            transport_mode="PULL_EXPORT",
        )


def test_path_exemption_does_not_cover_sibling_fields() -> None:
    from ai_hub_platform.modules.ingest import contract_store as store_mod
    from ai_hub_platform.modules.ingest.contract import ContractIssue

    exemption = {
        "items": [
            {
                "code": "schema_mismatch",
                "object_id": "C-1",
                "path": "customer.name",
            }
        ]
    }
    wildcard = {"items": [{"code": "schema_mismatch", "object_id": "C-1"}]}
    name_issue = ContractIssue(
        "schema_mismatch", "bad name", object_id="C-1", path="customer.name"
    )
    email_issue = ContractIssue(
        "schema_mismatch", "bad email", object_id="C-1", path="customer.email"
    )
    is_exempted = store_mod._issue_is_exempted  # pyright: ignore[reportPrivateUsage]
    assert is_exempted(name_issue, exemption)
    assert not is_exempted(email_issue, exemption)
    assert is_exempted(email_issue, wildcard)


@pytest.mark.asyncio
async def test_final_approval_rebinds_and_rejects_expired_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestSourceRow,
    )

    store = IngestContractStore()
    cert_id = uuid4()
    schema = {"type": "object"}
    fingerprint = schema_fingerprint(schema)
    first = _certification_row(
        certification_id=cert_id,
        data_owner_approved_by="alice",
        schema_fingerprint=fingerprint,
    )
    second = _certification_row(
        certification_id=cert_id,
        data_owner_approved_by="alice",
        operator_approved_by="bob",
        schema_fingerprint=fingerprint,
    )
    gets = {"n": 0}

    async def fake_get(*args: object, **kwargs: object) -> IngestCertificationRow:
        del args, kwargs
        gets["n"] += 1
        return first if gets["n"] == 1 else second

    async def fake_contract(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return IngestContractRow(
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema=schema,
            schema_fingerprint=fingerprint,
            field_classifications={},
            compatibility_mode="BACKWARD",
            origin="MANUAL",
            status="ACTIVE",
            reviewed_by="owner",
            reviewed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def fake_bind(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise IngestContractError("conformance evidence has expired")

    async def fake_source(*args: object, **kwargs: object) -> IngestSourceRow:
        del args, kwargs
        return IngestSourceRow(
            config=IngestSourceConfig.model_validate(
                {
                    "source_application_id": "e10-adapter",
                    "object_type": "erp.item",
                    "export_base_url": "http://app.test",
                }
            ),
            updated_at=datetime.now(UTC),
        )

    class _Result:
        rowcount = 1

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Result:
            del args, kwargs
            return _Result()

    monkeypatch.setattr(store, "get_certification", fake_get)
    monkeypatch.setattr(store, "get_contract", fake_contract)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.bind_certification_evidence",
        fake_bind,
    )
    monkeypatch.setattr(IngestConfigStore, "get_source", fake_source)

    with pytest.raises(IngestContractError, match="expired"):
        await store.approve_certification(
            _Session(),  # type: ignore[arg-type]
            certification_id=cert_id,
            role="operator",
            actor="bob",
        )


@pytest.mark.asyncio
async def test_final_approval_rejects_overwritten_contract_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestSourceRow,
    )

    store = IngestContractStore()
    cert_id = uuid4()
    original = {"type": "object", "properties": {"name": {"type": "string"}}}
    overwritten = {"type": "object", "properties": {"sku": {"type": "string"}}}
    first = _certification_row(
        certification_id=cert_id,
        data_owner_approved_by="alice",
        schema_fingerprint=schema_fingerprint(original),
    )
    second = _certification_row(
        certification_id=cert_id,
        data_owner_approved_by="alice",
        operator_approved_by="bob",
        schema_fingerprint=schema_fingerprint(original),
    )
    gets = {"n": 0}

    async def fake_get(*args: object, **kwargs: object) -> IngestCertificationRow:
        del args, kwargs
        gets["n"] += 1
        return first if gets["n"] == 1 else second

    async def fake_contract(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return IngestContractRow(
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema=overwritten,
            schema_fingerprint=schema_fingerprint(overwritten),
            field_classifications={},
            compatibility_mode="BACKWARD",
            origin="MANUAL",
            status="DRAFT",
            reviewed_by=None,
            reviewed_at=None,
            updated_at=datetime.now(UTC),
        )

    async def fake_bind(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("must not replay against an overwritten schema")

    async def fake_source(*args: object, **kwargs: object) -> IngestSourceRow:
        del args, kwargs
        return IngestSourceRow(
            config=IngestSourceConfig.model_validate(
                {
                    "source_application_id": "e10-adapter",
                    "object_type": "erp.item",
                    "export_base_url": "http://app.test",
                }
            ),
            updated_at=datetime.now(UTC),
        )

    class _Result:
        rowcount = 1

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Result:
            del args, kwargs
            return _Result()

    monkeypatch.setattr(store, "get_certification", fake_get)
    monkeypatch.setattr(store, "get_contract", fake_contract)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.bind_certification_evidence",
        fake_bind,
    )
    monkeypatch.setattr(IngestConfigStore, "get_source", fake_source)

    with pytest.raises(IngestContractConflictError, match="schema_fingerprint"):
        await store.approve_certification(
            _Session(),  # type: ignore[arg-type]
            certification_id=cert_id,
            role="operator",
            actor="bob",
        )


@pytest.mark.asyncio
async def test_final_approval_rejects_rejected_contract_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestSourceRow,
    )

    store = IngestContractStore()
    cert_id = uuid4()
    schema = {"type": "object"}
    fingerprint = schema_fingerprint(schema)
    first = _certification_row(
        certification_id=cert_id,
        data_owner_approved_by="alice",
        schema_fingerprint=fingerprint,
    )
    second = _certification_row(
        certification_id=cert_id,
        data_owner_approved_by="alice",
        operator_approved_by="bob",
        schema_fingerprint=fingerprint,
    )
    gets = {"n": 0}

    async def fake_get(*args: object, **kwargs: object) -> IngestCertificationRow:
        del args, kwargs
        gets["n"] += 1
        return first if gets["n"] == 1 else second

    async def fake_contract(*args: object, **kwargs: object) -> IngestContractRow:
        del args, kwargs
        return IngestContractRow(
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema=schema,
            schema_fingerprint=fingerprint,
            field_classifications={},
            compatibility_mode="BACKWARD",
            origin="MANUAL",
            status="REJECTED",
            reviewed_by="owner",
            reviewed_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def fake_bind(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("must not approve against a rejected contract")

    async def fake_source(*args: object, **kwargs: object) -> IngestSourceRow:
        del args, kwargs
        return IngestSourceRow(
            config=IngestSourceConfig.model_validate(
                {
                    "source_application_id": "e10-adapter",
                    "object_type": "erp.item",
                    "export_base_url": "http://app.test",
                }
            ),
            updated_at=datetime.now(UTC),
        )

    class _Result:
        rowcount = 1

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Result:
            del args, kwargs
            return _Result()

    monkeypatch.setattr(store, "get_certification", fake_get)
    monkeypatch.setattr(store, "get_contract", fake_contract)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.bind_certification_evidence",
        fake_bind,
    )
    monkeypatch.setattr(IngestConfigStore, "get_source", fake_source)

    with pytest.raises(IngestContractConflictError, match="no longer approvable"):
        await store.approve_certification(
            _Session(),  # type: ignore[arg-type]
            certification_id=cert_id,
            role="operator",
            actor="bob",
        )


def test_create_certification_stamps_source_transport_mode() -> None:
    create = inspect.getsource(IngestContractStore.create_certification)
    assert "transport_mode" in create
    assert "_registered_transport_mode" in create
    assert "lock_ingest_source" in create
    assert "for_update=True" in create
    assert "schema_fingerprint(contract.json_schema)" in create
    assert create.index("lock_ingest_source") < create.index("get_contract")
    assert create.index("get_contract") < create.index("bind_certification_evidence")
    assert create.index("lock_ingest_source") < create.index(
        "bind_certification_evidence"
    )
    assert create.index("_registered_transport_mode") < create.index(
        "bind_certification_evidence"
    )


@pytest.mark.asyncio
async def test_create_certification_rereads_contract_under_source_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    contract = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema={"type": "object"},
        schema_fingerprint=schema_fingerprint({"type": "object"}),
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="DRAFT",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=datetime.now(UTC),
    )
    order: list[str] = []

    async def fake_lock(*args: object, **kwargs: object) -> None:
        del args, kwargs
        order.append("lock")

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args
        assert kwargs.get("for_update") is True
        order.append("read")
        return contract

    class _Empty:
        def all(self) -> list[object]:
            return []

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Empty:
            return _Empty()

    async def fake_mode(*args: object, **kwargs: object) -> str:
        del args, kwargs
        return "PULL_EXPORT"

    monkeypatch.setattr(store, "get_contract", fake_get)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.lock_ingest_source",
        fake_lock,
    )
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store._registered_transport_mode",
        fake_mode,
    )
    refs = (str(uuid4()), str(uuid4()), str(uuid4()))
    with pytest.raises(IngestContractError, match="loaded Raw ingest batches"):
        await store.create_certification(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            rows_validated=1,
            observation_batch_from=uuid4(),
            observation_batch_to=uuid4(),
            violation_summary={"unexempted": []},
            exemption_summary={"items": []},
            full_regression_status="passed",
            incremental_regression_status="passed",
            rollback_drill_status="passed",
            full_regression_evidence_ref=refs[0],
            incremental_regression_evidence_ref=refs[1],
            rollback_drill_evidence_ref=refs[2],
        )
    assert order == ["lock", "read"]


@pytest.mark.asyncio
async def test_create_certification_rejects_draft_fingerprint_drift_under_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    contract = IngestContractRow(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema={"type": "object", "properties": {"sku": {"type": "string"}}},
        schema_fingerprint="a" * 64,
        field_classifications={},
        compatibility_mode="BACKWARD",
        origin="MANUAL",
        status="DRAFT",
        reviewed_by=None,
        reviewed_at=None,
        updated_at=datetime.now(UTC),
    )
    order: list[str] = []

    async def fake_lock(*args: object, **kwargs: object) -> None:
        del args, kwargs
        order.append("lock")

    async def fake_get(*args: object, **kwargs: object) -> IngestContractRow:
        del args
        assert kwargs.get("for_update") is True
        order.append("read")
        return contract

    monkeypatch.setattr(store, "get_contract", fake_get)
    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.contract_store.lock_ingest_source",
        fake_lock,
    )
    refs = (str(uuid4()), str(uuid4()), str(uuid4()))
    with pytest.raises(IngestContractConflictError, match="schema_fingerprint"):
        await store.create_certification(
            object(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            rows_validated=1,
            observation_batch_from=uuid4(),
            observation_batch_to=uuid4(),
            violation_summary={"unexempted": []},
            exemption_summary={"items": []},
            full_regression_status="passed",
            incremental_regression_status="passed",
            rollback_drill_status="passed",
            full_regression_evidence_ref=refs[0],
            incremental_regression_evidence_ref=refs[1],
            rollback_drill_evidence_ref=refs[2],
        )
    assert order == ["lock", "read"]


def test_activate_certification_matches_current_transport_mode() -> None:
    from ai_hub_platform.modules.ingest import contract_store as store_mod

    source = inspect.getsource(store_mod)
    assert "AND status = 'APPROVED'" in source
    assert "AND transport_mode = :transport_mode" in source
    assert "row.config.transport_mode" in source


@pytest.mark.asyncio
async def test_bind_certification_rejects_pull_batches_for_push_source() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Rows:
            del args, kwargs
            return _Rows(
                [
                    _BatchRow(batch_from, fingerprint=fingerprint),
                    _BatchRow(batch_to, fingerprint=fingerprint),
                ]
            )

    with pytest.raises(IngestContractError, match="transport_mode"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={"type": "object"},
            rows_validated=1,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(uuid4()),
            incremental_regression_evidence_ref=str(uuid4()),
            rollback_drill_evidence_ref=str(uuid4()),
            transport_mode="PUSH_AGENT",
        )


@pytest.mark.asyncio
async def test_bind_certification_rejects_verified_at_before_finished_at() -> None:
    batch_from = uuid4()
    batch_to = uuid4()
    fingerprint = "a" * 64
    started = datetime.now(UTC) - timedelta(hours=2)
    finished = datetime.now(UTC)
    mid = started + timedelta(hours=1)
    run_ids = (uuid4(), uuid4(), uuid4())
    kinds = ("full_regression", "incremental_regression", "rollback_drill")
    checks = [
        _RuntimeCheck(
            run_id,
            kind=kind,
            fingerprint=fingerprint,
            now=mid,
            expires_at=finished + timedelta(days=1),
            completed_at=finished + timedelta(minutes=5),
        )
        for run_id, kind in zip(run_ids, kinds, strict=True)
    ]

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Rows:
            del params
            sql = str(statement)
            if "raw_ingest_batch" in sql and "raw_change_record" not in sql:
                return _Rows(
                    [
                        _BatchRow(
                            batch_from,
                            fingerprint=fingerprint,
                            started_at=started,
                            finished_at=finished,
                        ),
                        _BatchRow(
                            batch_to,
                            fingerprint=fingerprint,
                            started_at=started,
                            finished_at=finished,
                        ),
                    ]
                )
            if "record.object_id" in sql:
                return _Rows([_ChangeRow({"name": "ok"})])
            if "conformance_run" in sql:
                return _Rows(list(checks))
            raise AssertionError(sql)

    with pytest.raises(IngestContractError, match="observation window"):
        await bind_certification_evidence(
            _Session(),  # type: ignore[arg-type]
            source_application_id="e10-adapter",
            object_type="erp.item",
            schema_fingerprint=fingerprint,
            json_schema={"type": "object"},
            rows_validated=1,
            observation_batch_from=batch_from,
            observation_batch_to=batch_to,
            exemption_summary={"items": []},
            full_regression_evidence_ref=str(run_ids[0]),
            incremental_regression_evidence_ref=str(run_ids[1]),
            rollback_drill_evidence_ref=str(run_ids[2]),
            transport_mode="PULL_EXPORT",
        )


@pytest.mark.asyncio
async def test_infer_draft_keeps_newest_change_record_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = IngestContractStore()
    captured: dict[str, object] = {}
    batch_id = uuid4()

    class _Sample:
        def __init__(
            self,
            object_id: str,
            payload: dict[str, object],
            *,
            version: int,
            batch_id: UUID | None = None,
        ) -> None:
            self.object_id = object_id
            self.payload = payload
            self.batch_id = batch_id
            self.version = version
            self.updated_at = datetime.now(UTC)

    class _Result:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def all(self) -> list[object]:
            return self._rows

    class _Session:
        async def execute(self, statement: object, params: object = None) -> _Result:
            sql = str(statement)
            assert isinstance(params, dict)
            if "raw_current_state" in sql:
                return _Result([])
            if "object_ids" in params:
                return _Result(
                    [_Sample("I-old", {"name": "new"}, version=5, batch_id=batch_id)]
                )
            if "raw_change_record" in sql:
                assert "DISTINCT ON (record.object_id)" in sql
                assert "COALESCE(batch.purpose, 'production') = 'production'" in sql
                return _Result(
                    [
                        _Sample("I-old", {"name": "new"}, version=5, batch_id=batch_id),
                        _Sample("I-old", {"name": "old"}, version=1, batch_id=batch_id),
                    ]
                )
            raise AssertionError(sql)

    async def fake_save(*args: object, **kwargs: object) -> IngestContractRow:
        del args
        captured.update(kwargs)
        return IngestContractRow(
            source_application_id="e10-adapter",
            object_type="erp.item",
            contract_version="item.v1",
            json_schema={"type": "object"},
            schema_fingerprint="a" * 64,
            field_classifications={},
            compatibility_mode="BACKWARD",
            origin="INFERRED_FROM_RAW",
            status="DRAFT",
            reviewed_by=None,
            reviewed_at=None,
            updated_at=datetime.now(UTC),
        )

    monkeypatch.setattr(store, "save_draft", fake_save)
    await store.infer_draft_from_raw(
        _Session(),  # type: ignore[arg-type]
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
    )
    evidence = json.loads(str(captured["inference_evidence_ref"]))
    assert evidence["samples"] == [
        {
            "object_id": "I-old",
            "version": 5,
            "batch_id": str(batch_id),
            "origin": "change_record",
        }
    ]
