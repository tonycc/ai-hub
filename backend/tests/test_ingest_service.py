"""Unit tests for raw ingest helpers and current-state / tombstone semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

import pytest
from ai_hub_platform.modules.ingest.service import (
    IngestRecord,
    IngestService,
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
        if (
            not bypass
            and existing is None
            and any(
                logged[:3] == key and logged[3] > record.version for logged in self.changes
            )
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
    assert "SELECT operation, content_hash" in insert
    assert ":purpose" in insert
    conflict = insert.split("ON CONFLICT", 1)[1].split("DO NOTHING", 1)[0]
    assert "purpose" in conflict
    assert "AND purpose = :purpose" in insert
    assert "already exists with a different purpose" not in insert
    assert "IngestRecordConflictError" in insert
    assert IngestRecordConflictError.error_code == "record_version_conflict"


class _TombstoneResult:
    def __init__(self, rows: list[tuple[str, int]] | None = None) -> None:
        self._rows = rows or []

    def all(self) -> list[tuple[str, int]]:
        return self._rows

    def scalar_one_or_none(self) -> int:
        return 1


class _TombstoneSession:
    """Serves current-state rows and records which objects get deleted."""

    def __init__(self, rows: list[tuple[str, int]]) -> None:
        self.rows = rows
        self.deleted: list[str] = []
        self.logged: list[tuple[str, int]] = []

    async def execute(self, statement: object, params: dict[str, Any]) -> _TombstoneResult:
        sql = str(statement)
        if "SELECT object_id, version" in sql:
            return _TombstoneResult(self.rows)
        if "INSERT INTO platform_raw.raw_change_record" in sql:
            self.logged.append((params["object_id"], params["version"]))
        elif "DELETE FROM platform_raw.raw_current_state" in sql:
            self.deleted.append(params["object_id"])
        return _TombstoneResult()


async def _synthesize(session: _TombstoneSession, *, max_version: int | None) -> int:
    return await IngestService()._synthesize_full_tombstones(  # pyright: ignore[reportPrivateUsage]
        cast(Any, session),
        batch_id=uuid4(),
        source_application_id="app-a",
        object_type="device",
        exported_object_ids={"E-keep"},
        high_watermark=95,
        payload_contract_version="device.v1",
        purpose="production",
        max_version=max_version,
    )


@pytest.mark.asyncio
async def test_full_tombstones_skip_rows_written_after_the_snapshot_fetch_began() -> None:
    # Before the fetch, current state topped out at version 100. E-new@120 was
    # ingested by another worker while the snapshot was being fetched.
    session = _TombstoneSession([("E-keep", 50), ("E-gone", 100), ("E-new", 120)])
    tombstones = await _synthesize(session, max_version=100)
    assert tombstones == 1
    assert session.deleted == ["E-gone"]
    # Absence stays authoritative for known rows even when hw (95) is lower.
    assert session.logged == [("E-gone", 101)]


@pytest.mark.asyncio
async def test_full_tombstones_without_a_ceiling_cover_every_absent_row() -> None:
    session = _TombstoneSession([("E-keep", 50), ("E-gone", 100), ("E-new", 120)])
    tombstones = await _synthesize(session, max_version=None)
    assert tombstones == 2
    assert session.deleted == ["E-gone", "E-new"]


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _ApplySession:
    """Current-state row and change-log lookups for ``_apply_current_state``."""

    def __init__(self, *, current_version: int | None, newer_logged: bool) -> None:
        self.current_version = current_version
        self.newer_logged = newer_logged
        self.writes: list[str] = []

    async def execute(self, statement: object, params: dict[str, Any]) -> _ScalarResult:
        del params
        sql = str(statement)
        if "FROM platform_raw.raw_change_record" in sql:
            return _ScalarResult(1 if self.newer_logged else None)
        if "SELECT version" in sql:
            return _ScalarResult(self.current_version)
        self.writes.append(sql.split()[0])
        return _ScalarResult(None)


async def _apply(session: _ApplySession, record: IngestRecord, *, bypass: bool = False) -> str:
    return await IngestService()._apply_current_state(  # pyright: ignore[reportPrivateUsage]
        cast(Any, session),
        source_application_id="app-a",
        object_type="device",
        record=record,
        payload_contract_version="device.v1",
        bypass_version_check=bypass,
    )


@pytest.mark.asyncio
async def test_stale_upsert_cannot_resurrect_an_object_deleted_at_a_newer_version() -> None:
    # E-1 was deleted at version 180: no current-state row, but the log has it.
    session = _ApplySession(current_version=None, newer_logged=True)
    applied = await _apply(session, IngestRecord("E-1", "upsert", 150, {"name": "old"}))
    assert applied == "skipped"
    assert session.writes == []


@pytest.mark.asyncio
async def test_upsert_after_delete_applies_when_it_is_the_newest_logged_version() -> None:
    session = _ApplySession(current_version=None, newer_logged=False)
    applied = await _apply(session, IngestRecord("E-1", "upsert", 200, {"name": "back"}))
    assert applied == "upsert"
    assert session.writes == ["INSERT"]


@pytest.mark.asyncio
async def test_full_tombstone_still_bypasses_version_and_log_checks() -> None:
    session = _ApplySession(current_version=100, newer_logged=True)
    applied = await _apply(session, IngestRecord("E-1", "delete", 101, None), bypass=True)
    assert applied == "delete"
    assert session.writes == ["DELETE"]


def test_stale_upsert_after_delete_stays_deleted_in_reference_store() -> None:
    store = InMemoryRawStore()

    def load(record: IngestRecord, high_watermark: int) -> dict[str, int]:
        return store.load_batch(
            source_application_id="app-a",
            object_type="device",
            sync_mode="incremental",
            records=[record],
            high_watermark=high_watermark,
            payload_contract_version="device.v1",
        )

    load(IngestRecord("E-1", "upsert", 150, {"name": "a"}), 150)
    load(IngestRecord("E-1", "delete", 180, None), 180)
    replayed = load(IngestRecord("E-1", "upsert", 150, {"name": "a"}), 180)
    assert replayed["upserts"] == 0
    assert ("app-a", "device", "E-1") not in store.current
