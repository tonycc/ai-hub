"""Protocol simulator for PUSH_AGENT generations (C1-A, no data2agent)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from ai_hub_platform.modules.ingest.contract import RegisteredContract, schema_fingerprint
from ai_hub_platform.modules.ingest.generation import (
    PUSH_MAX_GENERATION_LIFETIME,
    STAGING_RETENTION,
    GenerationState,
    InMemoryGenerationStore,
    PushGenerationService,
    PushIngestError,
    StagingRecord,
    batch_content_sha256,
    ordered_batch_digest,
)
from ai_hub_platform.modules.ingest.service import IngestRecord
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig

pytestmark = pytest.mark.asyncio


def _source(**overrides: object) -> IngestSourceConfig:
    payload: dict[str, object] = {
        "source_application_id": "e10-adapter",
        "object_type": "erp.item",
        "transport_mode": "PUSH_AGENT",
        "push_protocol_version": "1",
        "contract_validation_mode": "ENFORCE",
    }
    payload.update(overrides)
    return IngestSourceConfig.model_validate(payload)


def _contract() -> RegisteredContract:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    return RegisteredContract(
        source_application_id="e10-adapter",
        object_type="erp.item",
        contract_version="item.v1",
        json_schema=schema,
        schema_fingerprint=schema_fingerprint(schema),
        status="ACTIVE",
    )


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 29, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _service(
    source: IngestSourceConfig | None = None,
    *,
    batch_row_limit: int = 5_000,
) -> tuple[
    PushGenerationService, InMemoryGenerationStore, IngestSourceConfig, RegisteredContract
]:
    store = InMemoryGenerationStore()
    clock = _Clock()
    service = PushGenerationService(
        store, clock=clock, batch_row_limit=batch_row_limit
    )
    return service, store, source or _source(), _contract()


async def _open(
    service: PushGenerationService,
    source: IngestSourceConfig,
    contract: RegisteredContract,
    *,
    sync_mode: str = "incremental",
    external_id: str = "gen-1",
) -> GenerationState:
    return await service.create_generation(
        source=source,
        contract=contract,
        caller_application_id=source.source_application_id,
        external_generation_id=external_id,
        sync_mode=sync_mode,  # type: ignore[arg-type]
        request={"external_generation_id": external_id, "sync_mode": sync_mode},
    )


async def test_incremental_upsert_delete_and_replay() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract)
    upsert = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([upsert])
    first = await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[upsert],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    replay = await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[upsert],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    assert first["idempotent"] is False
    assert replay["idempotent"] is True
    deleted = IngestRecord("I-1", "delete", 2, None)
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=2,
        external_batch_id="b2",
        records=[deleted],
        high_watermark=2,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([deleted]),
    )
    batches = generation.accepted_batches
    completed = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=2,
        total_rows=2,
        ordered_batch_digest=ordered_batch_digest(batches),
        high_watermark=2,
    )
    assert completed.status == "COMPLETED"
    assert ("e10-adapter", "erp.item", "I-1") not in store.current
    again = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=2,
        total_rows=2,
        ordered_batch_digest=ordered_batch_digest(batches),
        high_watermark=2,
    )
    assert again.final_receipt == completed.final_receipt


async def test_full_pages_stage_then_publish_once_with_tombstones() -> None:
    service, store, source, contract = _service()
    store.current[("e10-adapter", "erp.item", "OLD")] = IngestRecord(
        "OLD", "upsert", 1, {"name": "gone"}
    )
    generation = await _open(service, source, contract, sync_mode="full")
    page1 = [IngestRecord("I-1", "upsert", 10, {"name": "a"})]
    page2 = [IngestRecord("I-2", "upsert", 11, {"name": "b"})]
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="p1",
        records=page1,
        high_watermark=10,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256(page1),
    )
    assert store.published_full_count == 0
    assert ("e10-adapter", "erp.item", "OLD") in store.current
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=2,
        external_batch_id="p2",
        records=page2,
        high_watermark=11,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256(page2),
    )
    completed = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=2,
        total_rows=2,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=11,
    )
    assert completed.status == "COMPLETED"
    assert store.published_full_count == 1
    assert ("e10-adapter", "erp.item", "I-1") in store.current
    assert ("e10-adapter", "erp.item", "I-2") in store.current
    assert ("e10-adapter", "erp.item", "OLD") not in store.current
    assert completed.final_receipt is not None
    assert completed.final_receipt["tombstones"] == 1


async def test_digest_conflict_sequence_gap_and_overlap() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract)
    record = IngestRecord("I-1", "upsert", 1, {"name": "a"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    with pytest.raises(PushIngestError) as gap:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=3,
            external_batch_id="b3",
            records=[record],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert gap.value.error_code == "sequence_gap"
    assert gap.value.details["expected_sequence_no"] == 2
    other = IngestRecord("I-1", "upsert", 1, {"name": "b"})
    with pytest.raises(PushIngestError) as conflict:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="b1",
            records=[other],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256([other]),
        )
    assert conflict.value.error_code == "batch_digest_conflict"
    with pytest.raises(PushIngestError) as overlap:
        await service.create_generation(
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            external_generation_id="gen-2",
            sync_mode="incremental",
            request={"external_generation_id": "gen-2", "sync_mode": "incremental"},
        )
    assert overlap.value.error_code == "generation_in_progress"
    _ = store


async def test_impersonation_schema_drift_lease_timeout_and_empty_full() -> None:
    service, store, source, contract = _service()
    with pytest.raises(PushIngestError) as impersonation:
        await service.create_generation(
            source=source,
            contract=contract,
            caller_application_id="other-app",
            external_generation_id="gen-x",
            sync_mode="incremental",
            request={"external_generation_id": "gen-x"},
        )
    assert impersonation.value.error_code == "source_impersonation_denied"
    generation = await _open(service, source, contract, sync_mode="full", external_id="full-1")
    bad = IngestRecord("I-1", "upsert", 1, {"name": "ok", "extra": True})
    with pytest.raises(PushIngestError) as drift:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="b1",
            records=[bad],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256([bad]),
        )
    assert drift.value.error_code == "ingest_contract_rejected"
    clock = service.clock
    assert isinstance(clock, _Clock)
    clock.advance(120)
    with pytest.raises(PushIngestError) as expired:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="b1",
            records=[IngestRecord("I-1", "upsert", 1, {"name": "ok"})],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256(
                [IngestRecord("I-1", "upsert", 1, {"name": "ok"})]
            ),
        )
    assert expired.value.error_code == "generation_expired"
    empty = await _open(service, source, contract, sync_mode="full", external_id="empty")
    failed_empty = await service.complete(
        empty.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=0,
        total_rows=0,
        ordered_batch_digest=ordered_batch_digest([]),
        high_watermark=0,
    )
    assert failed_empty.status == "FAILED"
    assert failed_empty.error_code == "empty_full_not_allowed"
    allowed = _source(allow_empty_full=True)
    allowed_gen = await service.create_generation(
        source=allowed,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="empty-ok",
        sync_mode="full",
        request={"external_generation_id": "empty-ok", "sync_mode": "full"},
    )
    completed = await service.complete(
        allowed_gen.generation_id,
        source=allowed,
        caller_application_id="e10-adapter",
        expected_batch_count=0,
        total_rows=0,
        ordered_batch_digest=ordered_batch_digest([]),
        high_watermark=0,
        confirm_empty_full=True,
    )
    assert completed.status == "COMPLETED"
    assert store.published_full_count == 1


async def test_empty_records_batch_cannot_bypass_empty_full_guard() -> None:
    service, _store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full", external_id="empty-page")
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="empty-batch",
        records=[],
        high_watermark=0,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([]),
    )
    failed_empty = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=0,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=0,
    )
    assert failed_empty.status == "FAILED"
    assert failed_empty.error_code == "empty_full_not_allowed"


async def test_invalid_ingest_record_is_rejected_before_write() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract)
    invalid = IngestRecord("I-1", "upsert", 0, {"name": "bolt"})
    with pytest.raises(PushIngestError) as error:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="b1",
            records=[invalid],
            high_watermark=0,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256([invalid]),
        )
    assert error.value.error_code == "invalid_ingest_record"
    assert store.current == {}
    assert generation.accepted_batches == []


async def test_disabled_or_pull_source_rejects_write_operations() -> None:
    service, _store, _configured, contract = _service()
    disabled = _source(enabled=False)
    with pytest.raises(PushIngestError) as disabled_err:
        await service.create_generation(
            source=disabled,
            contract=contract,
            caller_application_id="e10-adapter",
            external_generation_id="disabled",
            sync_mode="incremental",
            request={"external_generation_id": "disabled"},
        )
    assert disabled_err.value.error_code == "source_disabled"
    source = _source()
    generation = await _open(service, source, contract, external_id="then-disabled")
    later_disabled = _source(enabled=False)
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    with pytest.raises(PushIngestError) as submit_err:
        await service.submit_batch(
            generation.generation_id,
            source=later_disabled,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="b1",
            records=[record],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256([record]),
        )
    assert submit_err.value.error_code == "source_disabled"


async def test_disabled_source_replays_persisted_create_batch_and_complete() -> None:
    service, _store, source, contract = _service()
    request = {"external_generation_id": "keep", "sync_mode": "incremental"}
    generation = await service.create_generation(
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="keep",
        sync_mode="incremental",
        request=request,
    )
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    first = await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    batches = generation.accepted_batches
    completed = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(batches),
        high_watermark=1,
    )
    assert completed.status == "COMPLETED"
    disabled = _source(enabled=False)
    replayed = await service.create_generation(
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="keep",
        sync_mode="incremental",
        request=request,
    )
    assert replayed.generation_id == generation.generation_id
    replay_batch = await service.submit_batch(
        generation.generation_id,
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    assert replay_batch["idempotent"] is True
    assert replay_batch["content_sha256"] == first["content_sha256"]
    replay_complete = await service.complete(
        generation.generation_id,
        source=disabled,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(batches),
        high_watermark=1,
    )
    assert replay_complete.status == "COMPLETED"
    with pytest.raises(PushIngestError) as error:
        await service.create_generation(
            source=disabled,
            contract=contract,
            caller_application_id="e10-adapter",
            external_generation_id="new-after-disable",
            sync_mode="incremental",
            request={"external_generation_id": "new-after-disable"},
        )
    assert error.value.error_code == "source_disabled"


async def test_expired_active_generation_is_reclaimed_on_create() -> None:
    service, _store, source, contract = _service()
    first = await _open(service, source, contract, external_id="stale")
    clock = service.clock
    assert isinstance(clock, _Clock)
    clock.advance(120)
    second = await service.create_generation(
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="fresh",
        sync_mode="incremental",
        request={"external_generation_id": "fresh", "sync_mode": "incremental"},
    )
    stale = await service.get_generation(first.generation_id)
    assert stale.status == "EXPIRED"
    assert second.status == "OPEN"
    expired = await service.expire_stale()
    assert first.generation_id not in expired


async def test_source_scoped_batch_id_replays_or_conflicts_across_generations() -> None:
    service, _store, source, contract = _service()
    first = await _open(service, source, contract, sync_mode="full", external_id="full-a")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    original = await service.submit_batch(
        first.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-batch",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    await service.complete(
        first.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(first.accepted_batches),
        high_watermark=1,
    )
    second = await _open(service, source, contract, sync_mode="full", external_id="full-b")
    adopted = await service.submit_batch(
        second.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-batch",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    assert adopted["idempotent"] is True
    assert adopted["sequence_no"] == original["sequence_no"]
    assert second.accepted_batches == []
    assert original["content_sha256"] == adopted["content_sha256"]
    other = IngestRecord("I-2", "upsert", 2, {"name": "nut"})
    with pytest.raises(PushIngestError) as conflict:
        await service.submit_batch(
            second.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="shared-batch",
            records=[other],
            high_watermark=2,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256([other]),
        )
    assert conflict.value.error_code == "batch_digest_conflict"
    assert second.accepted_batches == []


async def test_generation_pins_contract_and_rejects_complete_watermark_regression() -> None:
    service, _store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full")
    assert generation.payload_contract_version == "item.v1"
    record = IngestRecord("I-1", "upsert", 5, {"name": "bolt"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[record],
        high_watermark=5,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    with pytest.raises(PushIngestError) as switched:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=2,
            external_batch_id="b2",
            records=[IngestRecord("I-2", "upsert", 6, {"name": "nut"})],
            high_watermark=6,
            payload_contract_version="item.v2",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256(
                [IngestRecord("I-2", "upsert", 6, {"name": "nut"})]
            ),
        )
    assert switched.value.error_code == "ingest_contract_rejected"
    with pytest.raises(PushIngestError) as watermark:
        await service.complete(
            generation.generation_id,
            source=source,
            caller_application_id="e10-adapter",
            expected_batch_count=1,
            total_rows=1,
            ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
            high_watermark=1,
        )
    assert watermark.value.error_code == "generation_complete_mismatch"
    assert watermark.value.details["minimum_high_watermark"] == 5


async def test_full_complete_rejects_watermark_below_committed_source_high_water() -> None:
    service, _store, source, contract = _service()
    first = await _open(service, source, contract, sync_mode="full", external_id="hw-a")
    record = IngestRecord("I-1", "upsert", 10, {"name": "bolt"})
    await service.submit_batch(
        first.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-hw-1",
        records=[record],
        high_watermark=10,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    await service.complete(
        first.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(first.accepted_batches),
        high_watermark=10,
    )
    second = await _open(service, source, contract, sync_mode="full", external_id="hw-b")
    stale = IngestRecord("I-1", "upsert", 4, {"name": "old"})
    await service.submit_batch(
        second.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-hw-2",
        records=[stale],
        high_watermark=4,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([stale]),
    )
    with pytest.raises(PushIngestError) as watermark:
        await service.complete(
            second.generation_id,
            source=source,
            caller_application_id="e10-adapter",
            expected_batch_count=1,
            total_rows=1,
            ordered_batch_digest=ordered_batch_digest(second.accepted_batches),
            high_watermark=4,
        )
    assert watermark.value.error_code == "generation_complete_mismatch"
    assert watermark.value.details["minimum_high_watermark"] == 10


async def test_completing_intent_survives_until_publish_or_worker_recovery() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract)
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    intent = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=1,
        publish=False,
    )
    assert intent.status == "COMPLETING"
    assert intent.completion_request is not None
    assert store.generations[generation.generation_id].status == "COMPLETING"
    recovered = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=1,
        publish=True,
    )
    assert recovered.status == "COMPLETED"

    later = await _open(service, source, contract, external_id="lease-recover")
    await service.submit_batch(
        later.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-recover",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    stuck = await service.complete(
        later.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(later.accepted_batches),
        high_watermark=1,
        publish=False,
    )
    assert stuck.status == "COMPLETING"
    clock = service.clock
    assert isinstance(clock, _Clock)
    clock.advance(121)
    recovered_ids = await service.recover_completing()
    assert later.generation_id in recovered_ids
    assert store.generations[later.generation_id].status == "COMPLETED"
    assert any(
        item.generation_id == later.generation_id
        and item.from_status == "COMPLETING"
        and item.to_status == "COMPLETING"
        and item.reason == "worker_recover"
        and item.actor == "system:worker_recover"
        and item.request_id == str(later.generation_id)
        for item in store.transitions
    )


async def test_sql_store_serializes_retries_and_starts_lease_reaper() -> None:
    import inspect

    from ai_hub_platform import main as main_mod
    from ai_hub_platform.api import ingest_push as push_api
    from ai_hub_platform.modules.ingest import generation as generation_mod
    from ai_hub_platform.modules.ingest import generation_sql as sql_mod
    from ai_hub_platform.modules.ingest import reconcile as reconcile_mod
    from ai_hub_platform.modules.ingest import scheduler as scheduler_mod
    from ai_hub_platform.modules.ingest import service as service_mod
    from ai_hub_platform.modules.ingest import source_lock as lock_mod

    module = inspect.getsource(sql_mod)
    assert "FOR UPDATE" in module
    assert "lock_ingest_source" in module
    assert "completion_request" in module
    assert "raw_push_committed_watermark" in module
    assert "expire_one" in module
    assert "recover_one" in module
    assert "worker_recover" in inspect.getsource(generation_mod.PushGenerationService.recover_one)
    assert "actor" in module
    assert "request_id" in module
    assert module.count("async with sessions()") >= 3
    assert "start_push_lease_reaper" in inspect.getsource(sql_mod)
    assert "start_push_lease_reaper" in inspect.getsource(main_mod.create_app)

    assert "lock_ingest_source" in inspect.getsource(service_mod)
    assert "lock_ingest_source" in inspect.getsource(reconcile_mod)
    assert "lock_ingest_source" in inspect.getsource(scheduler_mod)
    assert "publish=False" in inspect.getsource(push_api.complete_generation)
    assert "peek_generation" in inspect.getsource(push_api.submit_batch)
    assert "peek_generation" in inspect.getsource(push_api.heartbeat_generation)
    assert "peek_generation" in inspect.getsource(push_api.abort_generation)
    assert "peek_generation" in inspect.getsource(push_api.get_generation)
    assert "peek_generation" in inspect.getsource(push_api.complete_generation)
    push_api_src = inspect.getsource(push_api)
    assert "principal.token.subject" in push_api_src
    assert "request_id" in push_api_src
    create_src = inspect.getsource(push_api.create_generation)
    assert create_src.index("_raw_tx") < create_src.index("_lock_then_load_source")
    submit_src = inspect.getsource(push_api.submit_batch)
    assert submit_src.index("peek_generation") < submit_src.index("_lock_then_load_source")
    assert submit_src.index("peek_generation") < submit_src.index("IngestRecord")
    assert "batch_content_sha256" not in submit_src
    assert "page_limit_max" in submit_src
    create_svc = inspect.getsource(generation_mod.PushGenerationService.create_generation)
    assert create_svc.index("get_by_external") < create_svc.index(
        "_assert_writable_push_source"
    )
    submit_svc = inspect.getsource(generation_mod.PushGenerationService.submit_batch)
    assert submit_svc.index("_assert_batch_content_digest") < submit_svc.index(
        "_replay_batch"
    )
    assert submit_svc.index("_replay_batch") < submit_svc.index(
        "_assert_writable_push_source"
    )
    complete_svc = inspect.getsource(generation_mod.PushGenerationService.complete)
    assert complete_svc.index('status == "COMPLETED"') < complete_svc.index(
        "_assert_writable_push_source"
    )
    assert "max_length=PUSH_BATCH_RECORDS_ABSOLUTE_MAX" in inspect.getsource(
        push_api.SubmitBatchRequest
    )
    assert "ix_raw_push_generation_client_lease" in (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0003.py"
    ).read_text(encoding="utf-8")
    assert "raw_push_generation_transition" in (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0003.py"
    ).read_text(encoding="utf-8")
    transition_save = inspect.getsource(sql_mod.SqlGenerationStore.save)
    assert transition_save.count("CAST(:to_status AS varchar(20))") >= 10
    assert "request_id" in (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0004.py"
    ).read_text(encoding="utf-8")
    assert "purpose" in (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0005.py"
    ).read_text(encoding="utf-8")
    assert "audit_summary" in (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0005.py"
    ).read_text(encoding="utf-8")
    assert "max_batches" in inspect.getsource(push_api.CapabilitiesResponse)
    assert "purpose" in inspect.getsource(push_api.CreateGenerationRequest)
    assert "20260830_raw_0006" in (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0006.py"
    ).read_text(encoding="utf-8")
    assert "purpose" in (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0006.py"
    ).read_text(encoding="utf-8")
    raw_0006 = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260830_raw_0006.py"
    ).read_text(encoding="utf-8")
    assert "uq_raw_change_record_idempotent" in raw_0006
    upgrade = raw_0006.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "uq_raw_change_record_idempotent" not in upgrade
    assert "drop_constraint:uq_raw_change_record_idempotent" not in raw_0006
    assert "raw_change_record" in raw_0006
    raw_0007 = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "backend/migrations/versions/raw/20260831_raw_0007.py"
    ).read_text(encoding="utf-8")
    assert 'release_phase = "contract"' in raw_0007
    assert "uq_raw_change_record_idempotent_purpose" in raw_0007
    assert "rollback_compatible_with" not in raw_0007
    assert "AuditService" in inspect.getsource(push_api)
    assert "pg_advisory_xact_lock" in inspect.getsource(lock_mod)
    assert "AND raw_batch_id IS NULL" in module
    assert "SET generation_id = :generation_id" in module
    assert "ingest_policy_reload_failed" in inspect.getsource(scheduler_mod)
    assert "_require_locked_pull_source" in inspect.getsource(scheduler_mod)
    assert "purge_stale_staging" in module
    assert "IngestConfigStore" in module
    assert "push_staging_retention_hours" in module
    assert "DELETE FROM platform_raw.raw_push_staging" in module



async def test_protocol_version_and_batch_row_limit_are_enforced() -> None:
    service, _store, source, contract = _service(batch_row_limit=1)
    with pytest.raises(PushIngestError) as version_err:
        await service.create_generation(
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            external_generation_id="v2",
            sync_mode="incremental",
            request={"external_generation_id": "v2"},
            protocol_version="2",
        )
    assert version_err.value.error_code == "unsupported_protocol_version"
    generation = await _open(service, source, contract, external_id="limit")
    records = [
        IngestRecord("I-1", "upsert", 1, {"name": "a"}),
        IngestRecord("I-2", "upsert", 2, {"name": "b"}),
    ]
    with pytest.raises(PushIngestError) as limit_err:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="b1",
            records=records,
            high_watermark=2,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256(records),
        )
    assert limit_err.value.error_code == "batch_too_large"


async def test_incremental_batch_commits_watermark_before_generation_completes() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract)
    record = IngestRecord("I-1", "upsert", 10, {"name": "bolt"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-inc-hw",
        records=[record],
        high_watermark=10,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    await service.abort(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
    )
    assert store.committed_watermarks[("e10-adapter", "erp.item")] == 10
    full = await _open(service, source, contract, sync_mode="full", external_id="full-low")
    stale = IngestRecord("I-1", "upsert", 4, {"name": "old"})
    await service.submit_batch(
        full.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-full-low",
        records=[stale],
        high_watermark=4,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([stale]),
    )
    with pytest.raises(PushIngestError) as watermark:
        await service.complete(
            full.generation_id,
            source=source,
            caller_application_id="e10-adapter",
            expected_batch_count=1,
            total_rows=1,
            ordered_batch_digest=ordered_batch_digest(full.accepted_batches),
            high_watermark=4,
        )
    assert watermark.value.error_code == "generation_complete_mismatch"
    assert watermark.value.details["minimum_high_watermark"] == 10


async def test_incremental_replay_loads_aborted_full_batch_into_current() -> None:
    service, store, source, contract = _service()
    full = await _open(service, source, contract, sync_mode="full", external_id="full-abort")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        full.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-from-full",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    full_receipt = store.batch_receipts[
        ("e10-adapter", "erp.item", "shared-from-full", "production")
    ]
    assert full_receipt.generation_id == full.generation_id
    assert full_receipt.batch.raw_batch_id is None
    await service.abort(
        full.generation_id,
        source=source,
        caller_application_id="e10-adapter",
    )
    assert ("e10-adapter", "erp.item", "I-1") not in store.current
    incremental = await _open(service, source, contract, external_id="inc-replay")
    await service.submit_batch(
        incremental.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-from-full",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    assert store.current[("e10-adapter", "erp.item", "I-1")].version == 1
    receipt = store.batch_receipts[
        ("e10-adapter", "erp.item", "shared-from-full", "production")
    ]
    assert receipt.generation_id == incremental.generation_id
    assert receipt.batch.raw_batch_id is not None


async def test_aborted_full_receipt_is_adopted_and_bound_on_complete() -> None:
    service, store, source, contract = _service()
    first = await _open(
        service, source, contract, sync_mode="full", external_id="full-abort-adopt"
    )
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        first.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="adopt-full",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    await service.abort(
        first.generation_id,
        source=source,
        caller_application_id="e10-adapter",
    )
    second = await _open(
        service, source, contract, sync_mode="full", external_id="full-takeover"
    )
    adopted = await service.submit_batch(
        second.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="adopt-full",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    assert adopted["idempotent"] is False
    receipt = store.batch_receipts[
        ("e10-adapter", "erp.item", "adopt-full", "production")
    ]
    assert receipt.generation_id == second.generation_id
    assert receipt.batch.sequence_no == 1
    assert receipt.batch.raw_batch_id is None
    completed = await service.complete(
        second.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(second.accepted_batches),
        high_watermark=1,
    )
    assert completed.status == "COMPLETED"
    bound = store.batch_receipts[
        ("e10-adapter", "erp.item", "adopt-full", "production")
    ]
    assert bound.generation_id == second.generation_id
    assert bound.batch.raw_batch_id is not None
    assert store.published_full_count == 1
    third = await _open(
        service, source, contract, sync_mode="full", external_id="full-replay"
    )
    replayed = await service.submit_batch(
        third.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="adopt-full",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    assert replayed["idempotent"] is True
    assert third.accepted_batches == []
    assert store.published_full_count == 1


async def test_source_receipt_keeps_original_incremental_materialization() -> None:
    service, store, source, contract = _service()
    first = await _open(service, source, contract, external_id="inc-orig")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        first.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-keep",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    original = store.batch_receipts[
        ("e10-adapter", "erp.item", "shared-keep", "production")
    ]
    original_generation_id = original.generation_id
    original_raw_batch_id = original.batch.raw_batch_id
    original_high_watermark = original.batch.high_watermark
    assert original_raw_batch_id is not None
    await service.complete(
        first.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(first.accepted_batches),
        high_watermark=1,
    )
    full = await _open(service, source, contract, sync_mode="full", external_id="full-keep")
    with pytest.raises(PushIngestError) as drifted:
        await service.submit_batch(
            full.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="shared-keep",
            records=[record],
            high_watermark=9,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert drifted.value.error_code == "batch_digest_conflict"
    await service.abort(
        full.generation_id,
        source=source,
        caller_application_id="e10-adapter",
    )
    later = await _open(service, source, contract, external_id="inc-keep")
    await service.submit_batch(
        later.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-keep",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    final = store.batch_receipts[
        ("e10-adapter", "erp.item", "shared-keep", "production")
    ]
    assert final.generation_id == original_generation_id
    assert final.batch.raw_batch_id == original_raw_batch_id
    assert final.batch.high_watermark == original_high_watermark


async def test_materialized_replay_rejects_envelope_drift_and_keeps_watermark() -> None:
    service, store, source, contract = _service()
    first = await _open(service, source, contract, external_id="inc-env")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        first.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="env-batch",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    await service.complete(
        first.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(first.accepted_batches),
        high_watermark=1,
    )
    later = await _open(service, source, contract, external_id="inc-env-2")
    with pytest.raises(PushIngestError) as conflict:
        await service.submit_batch(
            later.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="env-batch",
            records=[record],
            high_watermark=100,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert conflict.value.error_code == "batch_digest_conflict"
    assert store.committed_watermarks[("e10-adapter", "erp.item")] == 1


async def test_same_generation_replay_rejects_envelope_drift() -> None:
    service, _store, source, contract = _service()
    generation = await _open(service, source, contract)
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="same-gen-env",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    with pytest.raises(PushIngestError) as conflict:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="same-gen-env",
            records=[record],
            high_watermark=100,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert conflict.value.error_code == "batch_digest_conflict"


async def test_replay_rejects_stolen_content_digest() -> None:
    service, _store, source, contract = _service()
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    stolen = IngestRecord("I-1", "upsert", 1, {"name": "other"})
    digest = batch_content_sha256([record])
    generation = await _open(service, source, contract, external_id="digest-active")
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="digest-batch",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    with pytest.raises(PushIngestError) as active:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="digest-batch",
            records=[stolen],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert active.value.error_code == "batch_digest_conflict"
    await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=1,
    )
    with pytest.raises(PushIngestError) as terminal:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="digest-batch",
            records=[stolen],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert terminal.value.error_code == "batch_digest_conflict"
    later = await _open(service, source, contract, external_id="digest-later")
    with pytest.raises(PushIngestError) as source_receipt:
        await service.submit_batch(
            later.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="digest-batch",
            records=[stolen],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert source_receipt.value.error_code == "batch_digest_conflict"


async def test_terminal_replay_rejects_envelope_drift() -> None:
    service, _store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="terminal-env",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    await service.abort(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
    )
    with pytest.raises(PushIngestError) as conflict:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="terminal-env",
            records=[record],
            high_watermark=100,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert conflict.value.error_code == "batch_digest_conflict"


async def test_terminal_full_staging_is_purged_after_diagnostic_ttl() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="stage-ttl",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=1,
    )
    assert store.staging[generation.generation_id]
    assert await service.purge_stale_staging() == 0
    store.updated_at[generation.generation_id] = (
        service.clock() - STAGING_RETENTION - timedelta(seconds=1)
    )
    assert await service.purge_stale_staging() == 1
    assert store.staging.get(generation.generation_id, []) == []
    assert store.generations[generation.generation_id].status == "COMPLETED"


async def test_full_cross_batch_duplicate_fails_without_stuck_completing() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full")
    first = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    second = IngestRecord("I-1", "upsert", 1, {"name": "nut"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="dup-a",
        records=[first],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([first]),
    )
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=2,
        external_batch_id="dup-b",
        records=[second],
        high_watermark=2,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([second]),
    )
    failed = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=2,
        total_rows=2,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=2,
    )
    assert failed.status == "FAILED"
    assert failed.error_code == "generation_complete_mismatch"
    assert store.generations[generation.generation_id].status == "FAILED"
    replacement = await _open(service, source, contract, external_id="after-fail")
    assert replacement.status == "OPEN"


async def test_completing_recovery_persists_unretryable_publish_failure() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-recover-fail",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    intent = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=1,
        publish=False,
    )
    assert intent.status == "COMPLETING"
    store.staging[generation.generation_id].append(
        StagingRecord(2, record, "item.v1")
    )
    clock = service.clock
    assert isinstance(clock, _Clock)
    clock.advance(121)
    recovered_ids = await service.recover_completing()
    assert generation.generation_id in recovered_ids
    assert store.generations[generation.generation_id].status == "FAILED"
    assert store.generations[generation.generation_id].error_code == (
        "generation_complete_mismatch"
    )


async def test_recover_completing_isolates_one_bad_generation() -> None:
    service, store, source, contract = _service()
    other_source = _source(object_type="erp.order")
    other_contract = RegisteredContract(
        source_application_id=contract.source_application_id,
        object_type="erp.order",
        contract_version=contract.contract_version,
        json_schema=contract.json_schema,
        schema_fingerprint=contract.schema_fingerprint,
        status=contract.status,
    )
    first = await _open(service, source, contract, sync_mode="full", external_id="iso-a")
    second = await _open(
        service, other_source, other_contract, sync_mode="full", external_id="iso-b"
    )
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    for generation, src, registered, batch_id in (
        (first, source, contract, "iso-a-b1"),
        (second, other_source, other_contract, "iso-b-b1"),
    ):
        await service.submit_batch(
            generation.generation_id,
            source=src,
            contract=registered,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id=batch_id,
            records=[record],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=registered.schema_fingerprint,
            content_sha256=batch_content_sha256([record]),
        )
        await service.complete(
            generation.generation_id,
            source=src,
            caller_application_id="e10-adapter",
            expected_batch_count=1,
            total_rows=1,
            ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
            high_watermark=1,
            publish=False,
        )
    original = service.recover_one

    async def boom(generation_id: UUID) -> UUID | None:
        if generation_id == first.generation_id:
            raise RuntimeError("permanent failure")
        return await original(generation_id)

    service.recover_one = boom  # type: ignore[method-assign]
    clock = service.clock
    assert isinstance(clock, _Clock)
    clock.advance(121)
    recovered_ids = await service.recover_completing()
    assert first.generation_id not in recovered_ids
    assert second.generation_id in recovered_ids
    assert store.generations[first.generation_id].status == "COMPLETING"
    assert store.generations[second.generation_id].status == "COMPLETED"


async def test_full_batch_rejects_duplicate_object_ids() -> None:
    service, _store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full")
    records = [
        IngestRecord("I-1", "upsert", 1, {"name": "bolt"}),
        IngestRecord("I-1", "upsert", 2, {"name": "bolt-2"}),
    ]
    with pytest.raises(PushIngestError, match="repeats object_id") as error:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="dup-full",
            records=records,
            high_watermark=2,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256(records),
        )
    assert error.value.status_code == 400


async def test_full_generation_rejects_duplicate_object_across_batches() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract, sync_mode="full")
    first = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    second = IngestRecord("I-1", "upsert", 2, {"name": "bolt-2"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="full-a",
        records=[first],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([first]),
    )
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=2,
        external_batch_id="full-b",
        records=[second],
        high_watermark=2,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([second]),
    )
    failed = await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=2,
        total_rows=2,
        ordered_batch_digest=ordered_batch_digest(
            store.generations[generation.generation_id].accepted_batches
        ),
        high_watermark=2,
    )
    assert failed.status == "FAILED"
    assert failed.error_code == "duplicate_object_in_full"


async def test_incremental_rejects_same_version_with_different_payload() -> None:
    service, _store, source, contract = _service()
    generation = await _open(service, source, contract)
    first = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    conflict = IngestRecord("I-1", "upsert", 1, {"name": "nut"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="inc-a",
        records=[first],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([first]),
    )
    with pytest.raises(PushIngestError, match="different content") as error:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=2,
            external_batch_id="inc-b",
            records=[conflict],
            high_watermark=1,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256([conflict]),
        )
    assert error.value.status_code == 409
    assert error.value.error_code == "record_version_conflict"


async def test_generation_transitions_record_actor_and_request_id() -> None:
    store = InMemoryGenerationStore()
    clock = _Clock()
    service = PushGenerationService(
        store,
        clock=clock,
        actor="svc:e10-adapter",
        request_id="req-42",
    )
    source = _source()
    contract = _contract()
    generation = await _open(service, source, contract)
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-actor",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(
            store.generations[generation.generation_id].accepted_batches
        ),
        high_watermark=1,
    )
    assert store.transitions
    assert all(
        item.actor == "svc:e10-adapter" and item.request_id == "req-42"
        for item in store.transitions
    )


async def test_publish_full_persists_generation_schema_fingerprint() -> None:
    import inspect

    from ai_hub_platform.modules.ingest import generation as generation_mod
    from ai_hub_platform.modules.ingest import generation_sql

    publish_call = inspect.getsource(generation_mod.PushGenerationService)
    assert "schema_fingerprint=generation.schema_fingerprint" in publish_call
    assert "apply_current_state=_applies_production_current_state" in publish_call
    sql_publish = inspect.getsource(generation_sql.SqlGenerationStore)
    assert "schema_fingerprint=schema_fingerprint" in sql_publish
    assert "schema_fingerprint: str | None = None" in sql_publish


async def test_cross_generation_full_replay_rejects_envelope_drift() -> None:
    service, _store, source, contract = _service()
    first = await _open(service, source, contract, sync_mode="full", external_id="full-1")
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    await service.submit_batch(
        first.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="full-shared",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    await service.complete(
        first.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(first.accepted_batches),
        high_watermark=1,
    )
    later = await _open(service, source, contract, sync_mode="full", external_id="full-2")
    with pytest.raises(PushIngestError) as conflict:
        await service.submit_batch(
            later.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="full-shared",
            records=[record],
            high_watermark=99,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=digest,
        )
    assert conflict.value.error_code == "batch_digest_conflict"


async def test_generation_transitions_record_expire_and_complete() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract)
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="tr-1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    await service.complete(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=1,
    )
    statuses = [(item.from_status, item.to_status) for item in store.transitions]
    assert (None, "OPEN") in statuses
    assert ("OPEN", "RECEIVING") in statuses or ("RECEIVING", "COMPLETING") in statuses
    assert any(item.to_status == "COMPLETED" for item in store.transitions)
    stale = await _open(service, source, contract, external_id="expire-me")
    clock = service.clock
    assert isinstance(clock, _Clock)
    clock.advance(121)
    expired = await service.expire_stale()
    assert stale.generation_id in expired
    assert any(
        item.generation_id == stale.generation_id
        and item.to_status == "EXPIRED"
        and item.reason == "client_lease_expired"
        for item in store.transitions
    )


async def test_push_request_models_reject_identifiers_longer_than_database() -> None:
    from ai_hub_platform.api.ingest_push import CreateGenerationRequest, PushRecord
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateGenerationRequest(
            source_application_id="e10-adapter",
            object_type="x" * 101,
            external_generation_id="g1",
            sync_mode="incremental",
        )
    with pytest.raises(ValidationError):
        CreateGenerationRequest(
            source_application_id="e10-adapter",
            object_type="erp.item",
            external_generation_id="g" * 201,
            sync_mode="incremental",
        )
    with pytest.raises(ValidationError):
        PushRecord(object_id="o" * 201, operation="upsert", version=1, payload={})
    with pytest.raises(ValidationError):
        PushRecord(object_id="ok", operation="upsert", version=0, payload={})
    with pytest.raises(ValidationError):
        PushRecord(
            object_id="ok",
            operation="upsert",
            version=10**100,
            payload={},
        )
    from ai_hub_platform.api.ingest_push import (
        CompleteGenerationRequest,
        SubmitBatchRequest,
    )

    with pytest.raises(ValidationError):
        SubmitBatchRequest(
            sequence_no=1,
            external_batch_id="b1",
            payload_contract_version="item.v1",
            high_watermark=10**100,
            content_sha256="a" * 64,
            schema_fingerprint="b" * 64,
            records=[],
        )
    with pytest.raises(ValidationError):
        CompleteGenerationRequest(
            expected_batch_count=0,
            total_rows=0,
            ordered_batch_digest="d",
            high_watermark=10**100,
        )


async def test_push_source_config_rejects_object_type_longer_than_raw_column() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="100 characters"):
        _source(object_type="x" * 101)


async def test_certification_purpose_allows_disabled_push_writes() -> None:
    service, store, _configured, contract = _service()
    disabled = _source(enabled=False)
    generation = await service.create_generation(
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="precert",
        sync_mode="incremental",
        request={"external_generation_id": "precert", "purpose": "certification"},
        purpose="certification",
    )
    assert generation.purpose == "certification"
    store.current[("e10-adapter", "erp.item", "I-prod")] = IngestRecord(
        "I-prod", "upsert", 9, {"name": "prod"}
    )
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    accepted = await service.submit_batch(
        generation.generation_id,
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    assert accepted["idempotent"] is False
    completed = await service.complete(
        generation.generation_id,
        source=disabled,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(generation.accepted_batches),
        high_watermark=1,
    )
    assert completed.status == "COMPLETED"
    assert ("e10-adapter", "erp.item", "I-1") not in store.current
    assert store.current[("e10-adapter", "erp.item", "I-prod")].payload == {"name": "prod"}
    assert store.changes[("e10-adapter", "erp.item", "I-1", 1, "certification")].payload == {
        "name": "bolt"
    }
    assert ("e10-adapter", "erp.item") not in store.committed_watermarks
    reasons = [
        item.reason
        for item in store.transitions
        if item.generation_id == generation.generation_id
    ]
    assert "create" in reasons
    assert "first_batch" in reasons
    assert "complete" in reasons
    assert "publish" in reasons


async def test_production_generation_does_not_reuse_certification_receipt() -> None:
    service, store, source, contract = _service()
    disabled = _source(enabled=False)
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    digest = batch_content_sha256([record])
    certification = await service.create_generation(
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="cert-shared",
        sync_mode="incremental",
        request={"external_generation_id": "cert-shared", "purpose": "certification"},
        purpose="certification",
    )
    await service.submit_batch(
        certification.generation_id,
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-b1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=digest,
    )
    await service.complete(
        certification.generation_id,
        source=disabled,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(certification.accepted_batches),
        high_watermark=1,
    )
    assert ("e10-adapter", "erp.item", "I-1") not in store.current

    production = await _open(service, source, contract, external_id="prod-shared")
    production_record = IngestRecord("I-2", "upsert", 1, {"name": "prod"})
    accepted = await service.submit_batch(
        production.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="shared-b1",
        records=[production_record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([production_record]),
    )
    assert accepted["idempotent"] is False
    completed = await service.complete(
        production.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(production.accepted_batches),
        high_watermark=1,
    )
    assert completed.status == "COMPLETED"
    assert ("e10-adapter", "erp.item", "I-1") not in store.current
    assert store.current[("e10-adapter", "erp.item", "I-2")].payload == {"name": "prod"}


async def test_contract_key_allows_cross_purpose_change_record_versions() -> None:
    service, store, source, contract = _service()
    disabled = _source(enabled=False)
    cert_record = IngestRecord("I-1", "upsert", 1, {"name": "cert"})
    production_record = IngestRecord("I-1", "upsert", 1, {"name": "prod"})
    certification = await service.create_generation(
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="cert-version",
        sync_mode="incremental",
        request={"external_generation_id": "cert-version", "purpose": "certification"},
        purpose="certification",
    )
    await service.submit_batch(
        certification.generation_id,
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="cert-v1",
        records=[cert_record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([cert_record]),
    )
    await service.complete(
        certification.generation_id,
        source=disabled,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(certification.accepted_batches),
        high_watermark=1,
    )
    production = await _open(service, source, contract, external_id="prod-version")
    accepted = await service.submit_batch(
        production.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="prod-v1",
        records=[production_record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([production_record]),
    )
    assert accepted["idempotent"] is False
    assert store.changes[
        ("e10-adapter", "erp.item", "I-1", 1, "certification")
    ].payload == {"name": "cert"}
    assert store.changes[
        ("e10-adapter", "erp.item", "I-1", 1, "production")
    ].payload == {"name": "prod"}
    assert store.current[("e10-adapter", "erp.item", "I-1")].payload == {
        "name": "prod"
    }


async def test_contract_key_keeps_same_content_separate_by_purpose() -> None:
    service, store, source, contract = _service()
    disabled = _source(enabled=False)
    record = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    certification = await service.create_generation(
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        external_generation_id="cert-same",
        sync_mode="incremental",
        request={"external_generation_id": "cert-same", "purpose": "certification"},
        purpose="certification",
    )
    await service.submit_batch(
        certification.generation_id,
        source=disabled,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="cert-same-v1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    await service.complete(
        certification.generation_id,
        source=disabled,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(certification.accepted_batches),
        high_watermark=1,
    )
    production = await _open(service, source, contract, external_id="prod-same")
    accepted = await service.submit_batch(
        production.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="prod-same-v1",
        records=[record],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    assert accepted["idempotent"] is False
    assert store.changes[
        ("e10-adapter", "erp.item", "I-1", 1, "certification")
    ].payload == {"name": "bolt"}
    assert store.changes[
        ("e10-adapter", "erp.item", "I-1", 1, "production")
    ].payload == {"name": "bolt"}
    assert store.current[("e10-adapter", "erp.item", "I-1")].payload == {
        "name": "bolt"
    }


async def test_full_publish_conflict_rolls_back_partial_raw_writes() -> None:
    service, store, source, contract = _service()
    seed = await _open(service, source, contract, external_id="seed")
    existing = IngestRecord("I-1", "upsert", 1, {"name": "bolt"})
    await service.submit_batch(
        seed.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="seed-b1",
        records=[existing],
        high_watermark=1,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([existing]),
    )
    await service.complete(
        seed.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(seed.accepted_batches),
        high_watermark=1,
    )
    full = await _open(service, source, contract, sync_mode="full", external_id="full-conflict")
    newer = IngestRecord("I-2", "upsert", 2, {"name": "ok"})
    conflict = IngestRecord("I-1", "upsert", 1, {"name": "nut"})
    await service.submit_batch(
        full.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="full-ok",
        records=[newer],
        high_watermark=2,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([newer]),
    )
    await service.submit_batch(
        full.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=2,
        external_batch_id="full-bad",
        records=[conflict],
        high_watermark=2,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([conflict]),
    )
    failed = await service.complete(
        full.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=2,
        total_rows=2,
        ordered_batch_digest=ordered_batch_digest(
            store.generations[full.generation_id].accepted_batches
        ),
        high_watermark=2,
    )
    assert failed.status == "FAILED"
    assert failed.error_code == "generation_complete_mismatch"
    assert ("e10-adapter", "erp.item", "I-2") not in store.current
    assert ("e10-adapter", "erp.item", "I-2", 2, "production") not in store.changes
    assert store.current[("e10-adapter", "erp.item", "I-1")].payload == {"name": "bolt"}
    assert store.changes[
        ("e10-adapter", "erp.item", "I-1", 1, "production")
    ].payload == {"name": "bolt"}


async def test_incremental_complete_rejects_high_watermark_regression() -> None:
    service, store, source, contract = _service()
    first = await _open(service, source, contract, external_id="hw-high")
    record = IngestRecord("I-1", "upsert", 10, {"name": "bolt"})
    await service.submit_batch(
        first.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="b-high",
        records=[record],
        high_watermark=10,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=batch_content_sha256([record]),
    )
    await service.complete(
        first.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        expected_batch_count=1,
        total_rows=1,
        ordered_batch_digest=ordered_batch_digest(first.accepted_batches),
        high_watermark=10,
    )
    later = await _open(service, source, contract, external_id="hw-low")
    stale = IngestRecord("I-new", "upsert", 4, {"name": "old"})
    with pytest.raises(PushIngestError) as error:
        await service.submit_batch(
            later.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=1,
            external_batch_id="b-low",
            records=[stale],
            high_watermark=4,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=batch_content_sha256([stale]),
        )
    assert error.value.error_code == "generation_complete_mismatch"
    assert error.value.details["minimum_high_watermark"] == 10
    assert ("e10-adapter", "erp.item", "I-new") not in store.current
    assert ("e10-adapter", "erp.item", "I-new", 4, "production") not in store.changes
    assert store.committed_watermarks[("e10-adapter", "erp.item")] == 10


async def test_digest_vectors_cover_unicode_delete_null_and_empty() -> None:
    from ai_hub_platform.modules.ingest.generation import AcceptedBatch
    from ai_hub_platform.modules.ingest.service import payload_content_hash

    assert (
        payload_content_hash(None)
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert (
        batch_content_sha256([])
        == "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    )
    assert (
        ordered_batch_digest([])
        == "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
    )
    deleted = IngestRecord("I-1", "delete", 1, None)
    assert (
        batch_content_sha256([deleted])
        == "64bb7f0724cf5c81f85b8f10396745271380f64bbe45ba560ce3829931f9b69f"
    )
    unicode_record = IngestRecord("I-2", "upsert", 2, {"name": "螺栓", "note": None})
    assert (
        batch_content_sha256([unicode_record])
        == "4b5ed0169d0b1201ecca2cf45e208b83ab14c8efb3f77c17e1fdff472fe4b121"
    )
    batches = [
        AcceptedBatch(
            1,
            "b1",
            batch_content_sha256([deleted]),
            1,
            1,
            "item.v1",
        ),
        AcceptedBatch(
            2,
            "b2",
            batch_content_sha256([unicode_record]),
            1,
            2,
            "item.v1",
        ),
    ]
    assert (
        ordered_batch_digest(batches)
        == "9c9fc5ebba13db82dad02a6ba670ffd08676d226b4b956030642a33d3be01ba8"
    )


async def test_generation_rejects_unbounded_empty_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_hub_platform.modules.ingest import generation as generation_mod

    monkeypatch.setattr(generation_mod, "PUSH_MAX_BATCHES", 2)
    service, store, source, contract = _service()
    generation = await _open(service, source, contract, external_id="caps")
    empty_digest = batch_content_sha256([])
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=1,
        external_batch_id="empty-1",
        records=[],
        high_watermark=0,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=empty_digest,
    )
    await service.submit_batch(
        generation.generation_id,
        source=source,
        contract=contract,
        caller_application_id="e10-adapter",
        sequence_no=2,
        external_batch_id="empty-2",
        records=[],
        high_watermark=0,
        payload_contract_version="item.v1",
        schema_fingerprint=contract.schema_fingerprint,
        content_sha256=empty_digest,
    )
    with pytest.raises(PushIngestError) as error:
        await service.submit_batch(
            generation.generation_id,
            source=source,
            contract=contract,
            caller_application_id="e10-adapter",
            sequence_no=3,
            external_batch_id="empty-3",
            records=[],
            high_watermark=0,
            payload_contract_version="item.v1",
            schema_fingerprint=contract.schema_fingerprint,
            content_sha256=empty_digest,
        )
    assert error.value.error_code == "generation_limit_exceeded"
    reasons = [
        item.reason
        for item in store.transitions
        if item.generation_id == generation.generation_id
    ]
    assert "first_batch" in reasons
    assert "batch" in reasons


async def test_production_purpose_still_rejects_disabled_push() -> None:
    service, _store, _configured, contract = _service()
    disabled = _source(enabled=False)
    with pytest.raises(PushIngestError) as error:
        await service.create_generation(
            source=disabled,
            contract=contract,
            caller_application_id="e10-adapter",
            external_generation_id="prod-disabled",
            sync_mode="incremental",
            request={"external_generation_id": "prod-disabled"},
            purpose="production",
        )
    assert error.value.error_code == "source_disabled"


async def test_heartbeat_caps_lease_to_max_generation_lifetime() -> None:
    service, _store, source, contract = _service()
    generation = await _open(service, source, contract, external_id="lease-cap")
    created = generation.created_at
    assert created is not None
    updated = await service.heartbeat(
        generation.generation_id,
        source=source,
        caller_application_id="e10-adapter",
        lease_seconds=int(PUSH_MAX_GENERATION_LIFETIME.total_seconds()) + 3600,
    )
    assert updated.client_lease_expires_at == created + PUSH_MAX_GENERATION_LIFETIME


async def test_reaper_expires_lifetime_even_with_fresh_lease() -> None:
    service, store, source, contract = _service()
    generation = await _open(service, source, contract, external_id="lifetime-fresh")
    stored = store.generations[generation.generation_id]
    stored.client_lease_expires_at = service.clock() + timedelta(hours=48)
    await store.save(stored)
    clock = service.clock
    assert isinstance(clock, _Clock)
    clock.advance(int(PUSH_MAX_GENERATION_LIFETIME.total_seconds()))
    expired = await service.expire_stale()
    assert generation.generation_id in expired
    assert store.generations[generation.generation_id].status == "EXPIRED"


async def test_sql_lease_candidates_include_absolute_lifetime() -> None:
    import inspect

    from ai_hub_platform.modules.ingest import generation_sql

    query = inspect.getsource(generation_sql.SqlGenerationStore.list_lease_candidates)
    assert "lifetime_cutoff" in query
    assert "created_at <=" in query
