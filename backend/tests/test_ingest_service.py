"""Unit tests for raw ingest helpers and current-state / tombstone semantics."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from ai_hub_platform.modules.ingest.service import (
    IngestRecord,
    IngestValidationError,
    payload_content_hash,
    should_apply_version,
    tombstone_version,
    validate_ingest_records,
)


def test_payload_content_hash_is_stable_and_order_independent() -> None:
    left = payload_content_hash({"b": 2, "a": 1})
    right = payload_content_hash({"a": 1, "b": 2})
    assert left == right
    assert payload_content_hash(None) == payload_content_hash(None)
    assert payload_content_hash(None) != left


def test_should_apply_version_rejects_stale_and_equal() -> None:
    assert should_apply_version(1, None) is True
    assert should_apply_version(5, 4) is True
    assert should_apply_version(4, 4) is False
    assert should_apply_version(3, 4) is False


def test_tombstone_version_covers_last_modified_object_edge_case() -> None:
    # Object at version 100 deleted; full high_watermark is 95.
    assert tombstone_version(100, 95) == 101
    assert tombstone_version(50, 95) == 95


def test_validate_ingest_records_enforces_contract() -> None:
    validate_ingest_records(
        [
            IngestRecord("E-1", "upsert", 1, {"name": "a"}),
            IngestRecord("E-2", "delete", 2, None),
        ],
        high_watermark=2,
    )
    with pytest.raises(IngestValidationError, match="payload"):
        validate_ingest_records(
            [IngestRecord("E-1", "upsert", 1, None)],
            high_watermark=1,
        )
    with pytest.raises(IngestValidationError, match="null payload"):
        validate_ingest_records(
            [IngestRecord("E-1", "delete", 1, {"x": 1})],
            high_watermark=1,
        )
    with pytest.raises(IngestValidationError, match="high_watermark"):
        validate_ingest_records(
            [IngestRecord("E-1", "upsert", 5, {"name": "a"})],
            high_watermark=4,
        )
    with pytest.raises(IngestValidationError, match="duplicate"):
        validate_ingest_records(
            [
                IngestRecord("E-1", "upsert", 1, {"name": "a"}),
                IngestRecord("E-1", "upsert", 1, {"name": "b"}),
            ],
            high_watermark=1,
        )


@dataclass
class _Current:
    version: int
    payload: dict[str, object] | None
    contract: str


@dataclass
class InMemoryRawStore:
    """Mirrors platform_raw idempotent log + current-state rules for unit tests."""

    changes: dict[tuple[str, str, str, int], IngestRecord] = field(
        default_factory=dict[tuple[str, str, str, int], IngestRecord]
    )
    current: dict[tuple[str, str, str], _Current] = field(
        default_factory=dict[tuple[str, str, str], _Current]
    )
    cursor: dict[tuple[str, str], int] = field(default_factory=dict[tuple[str, str], int])

    def load_batch(
        self,
        *,
        source_application_id: str,
        object_type: str,
        sync_mode: str,
        records: list[IngestRecord],
        high_watermark: int,
        payload_contract_version: str,
    ) -> dict[str, int]:
        validate_ingest_records(records, high_watermark=high_watermark)
        accepted = 0
        skipped = 0
        upserts = 0
        deletes = 0
        for record in records:
            key = (
                source_application_id,
                object_type,
                record.object_id,
                record.version,
            )
            if key in self.changes:
                skipped += 1
            else:
                self.changes[key] = record
                accepted += 1
            applied = self._apply(
                source_application_id,
                object_type,
                record,
                payload_contract_version,
                bypass=False,
            )
            if applied == "upsert":
                upserts += 1
            elif applied == "delete":
                deletes += 1

        tombstones = 0
        if sync_mode == "full":
            exported = {record.object_id for record in records}
            missing = [
                (object_id, row)
                for (app, kind, object_id), row in list(self.current.items())
                if app == source_application_id
                and kind == object_type
                and object_id not in exported
            ]
            for object_id, row in missing:
                version = tombstone_version(row.version, high_watermark)
                tombstone = IngestRecord(object_id, "delete", version, None)
                change_key = (
                    source_application_id,
                    object_type,
                    object_id,
                    version,
                )
                if change_key not in self.changes:
                    self.changes[change_key] = tombstone
                self._apply(
                    source_application_id,
                    object_type,
                    tombstone,
                    payload_contract_version,
                    bypass=True,
                )
                tombstones += 1
                deletes += 1
        return {
            "accepted": accepted,
            "skipped": skipped,
            "upserts": upserts,
            "deletes": deletes,
            "tombstones": tombstones,
        }

    def _apply(
        self,
        app: str,
        kind: str,
        record: IngestRecord,
        contract: str,
        *,
        bypass: bool,
    ) -> str:
        key = (app, kind, record.object_id)
        existing = self.current.get(key)
        if not bypass and not should_apply_version(
            record.version,
            existing.version if existing else None,
        ):
            return "skipped"
        if record.operation == "delete":
            self.current.pop(key, None)
            return "delete"
        self.current[key] = _Current(
            version=record.version,
            payload=dict(record.payload or {}),
            contract=contract,
        )
        return "upsert"


def test_incremental_upsert_delete_and_out_of_order_skip() -> None:
    store = InMemoryRawStore()
    first = store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="incremental",
        records=[
            IngestRecord("E-1", "upsert", 10, {"name": "lathe"}),
            IngestRecord("E-2", "upsert", 11, {"name": "mill"}),
        ],
        high_watermark=11,
        payload_contract_version="device.v1",
    )
    assert first["accepted"] == 2
    assert store.current[("app-a", "device", "E-1")].payload == {"name": "lathe"}

    stale = store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="incremental",
        records=[IngestRecord("E-1", "upsert", 9, {"name": "old"})],
        high_watermark=11,
        payload_contract_version="device.v1",
    )
    assert stale["upserts"] == 0
    assert store.current[("app-a", "device", "E-1")].payload == {"name": "lathe"}

    deleted = store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="incremental",
        records=[IngestRecord("E-2", "delete", 12, None)],
        high_watermark=12,
        payload_contract_version="device.v1",
    )
    assert deleted["deletes"] == 1
    assert ("app-a", "device", "E-2") not in store.current


def test_idempotent_replay_does_not_duplicate_change_log() -> None:
    store = InMemoryRawStore()
    records = [IngestRecord("E-1", "upsert", 1, {"name": "a"})]
    first = store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="incremental",
        records=records,
        high_watermark=1,
        payload_contract_version="device.v1",
    )
    second = store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="incremental",
        records=records,
        high_watermark=1,
        payload_contract_version="device.v1",
    )
    assert first["accepted"] == 1
    assert second["accepted"] == 0
    assert second["skipped"] == 1
    assert len(store.changes) == 1


def test_full_rebuild_tombstone_removes_absent_objects_even_when_hw_is_lower() -> None:
    store = InMemoryRawStore()
    store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="incremental",
        records=[
            IngestRecord("E-keep", "upsert", 50, {"name": "keep"}),
            IngestRecord("E-gone", "upsert", 100, {"name": "gone"}),
        ],
        high_watermark=100,
        payload_contract_version="device.v1",
    )
    # Full export only has E-keep; high_watermark 95 < E-gone's version 100.
    result = store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="full",
        records=[IngestRecord("E-keep", "upsert", 50, {"name": "keep"})],
        high_watermark=95,
        payload_contract_version="device.v1",
    )
    assert result["tombstones"] == 1
    assert ("app-a", "device", "E-gone") not in store.current
    assert ("app-a", "device", "E-keep") in store.current
    # Tombstone log version is max(100+1, 95) = 101
    assert ("app-a", "device", "E-gone", 101) in store.changes


def test_empty_full_export_tombstones_all_current_objects() -> None:
    store = InMemoryRawStore()
    store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="incremental",
        records=[IngestRecord("E-1", "upsert", 3, {"name": "x"})],
        high_watermark=3,
        payload_contract_version="device.v1",
    )
    result = store.load_batch(
        source_application_id="app-a",
        object_type="device",
        sync_mode="full",
        records=[],
        high_watermark=3,
        payload_contract_version="device.v1",
    )
    assert result["tombstones"] == 1
    assert store.current == {}


def test_change_record_conflict_compares_operation_and_content_hash() -> None:
    from pathlib import Path

    from ai_hub_platform.modules.ingest.service import IngestRecordConflictError

    source = (
        Path(__file__).resolve().parents[1]
        / "src/ai_hub_platform/modules/ingest/service.py"
    ).read_text(encoding="utf-8")
    insert = source.split("async def _insert_change_record", 1)[1].split(
        "async def ", 1
    )[0]
    assert "DO NOTHING" in insert
    assert "SELECT operation, content_hash, purpose" in insert
    assert ":purpose" in insert
    conflict = insert.split("ON CONFLICT", 1)[1].split("DO NOTHING", 1)[0]
    assert "purpose" not in conflict
    assert "AND purpose = :purpose" not in insert
    assert "already exists with a different purpose" in insert
    assert "IngestRecordConflictError" in insert
    assert IngestRecordConflictError.error_code == "record_version_conflict"
