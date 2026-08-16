"""Reconcile current state against raw change-log replay; rebuild helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_hub_platform.modules.ingest.service import (
    IngestRecord,
    Operation,
    payload_content_hash,
    should_apply_version,
)

RebuildMode = Literal["log", "source"]


@dataclass(frozen=True, slots=True)
class StateFingerprint:
    object_id: str
    version: int
    content_hash: str
    payload_contract_version: str | None
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ReconcileDrift:
    object_id: str
    kind: Literal["missing", "unexpected", "hash_mismatch", "version_mismatch"]
    expected_version: int | None = None
    actual_version: int | None = None
    expected_hash: str | None = None
    actual_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    source_application_id: str
    object_type: str
    expected_count: int
    actual_count: int
    drifted: bool
    drifts: tuple[ReconcileDrift, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_application_id": self.source_application_id,
            "object_type": self.object_type,
            "expected_count": self.expected_count,
            "actual_count": self.actual_count,
            "drifted": self.drifted,
            "drift_count": len(self.drifts),
            "drifts": [
                {
                    "object_id": drift.object_id,
                    "kind": drift.kind,
                    "expected_version": drift.expected_version,
                    "actual_version": drift.actual_version,
                    "expected_hash": drift.expected_hash,
                    "actual_hash": drift.actual_hash,
                }
                for drift in self.drifts
            ],
        }


@dataclass(frozen=True, slots=True)
class ChangeLogEntry:
    object_id: str
    operation: Operation
    version: int
    payload: Mapping[str, Any] | None
    payload_contract_version: str | None
    content_hash: str | None = None


def replay_change_log(entries: Sequence[ChangeLogEntry]) -> dict[str, StateFingerprint]:
    """Fold ordered change-log entries into expected current-state fingerprints."""
    ordered = sorted(entries, key=lambda entry: (entry.version, entry.object_id))
    state: dict[str, StateFingerprint] = {}
    for entry in ordered:
        existing = state.get(entry.object_id)
        existing_version = existing.version if existing is not None else None
        if not should_apply_version(entry.version, existing_version):
            continue
        if entry.operation == "delete":
            state.pop(entry.object_id, None)
            continue
        content_hash = entry.content_hash or payload_content_hash(entry.payload)
        state[entry.object_id] = StateFingerprint(
            object_id=entry.object_id,
            version=entry.version,
            content_hash=content_hash,
            payload_contract_version=entry.payload_contract_version,
            payload=None if entry.payload is None else dict(entry.payload),
        )
    return state


def compare_current_state(
    *,
    source_application_id: str,
    object_type: str,
    expected: Mapping[str, StateFingerprint],
    actual: Mapping[str, StateFingerprint],
) -> ReconcileReport:
    drifts: list[ReconcileDrift] = []
    for object_id, expected_row in expected.items():
        actual_row = actual.get(object_id)
        if actual_row is None:
            drifts.append(
                ReconcileDrift(
                    object_id=object_id,
                    kind="missing",
                    expected_version=expected_row.version,
                    expected_hash=expected_row.content_hash,
                )
            )
            continue
        if actual_row.version != expected_row.version:
            drifts.append(
                ReconcileDrift(
                    object_id=object_id,
                    kind="version_mismatch",
                    expected_version=expected_row.version,
                    actual_version=actual_row.version,
                    expected_hash=expected_row.content_hash,
                    actual_hash=actual_row.content_hash,
                )
            )
        elif actual_row.content_hash != expected_row.content_hash:
            drifts.append(
                ReconcileDrift(
                    object_id=object_id,
                    kind="hash_mismatch",
                    expected_version=expected_row.version,
                    actual_version=actual_row.version,
                    expected_hash=expected_row.content_hash,
                    actual_hash=actual_row.content_hash,
                )
            )
    for object_id, actual_row in actual.items():
        if object_id not in expected:
            drifts.append(
                ReconcileDrift(
                    object_id=object_id,
                    kind="unexpected",
                    actual_version=actual_row.version,
                    actual_hash=actual_row.content_hash,
                )
            )
    drifts.sort(key=lambda drift: (drift.kind, drift.object_id))
    return ReconcileReport(
        source_application_id=source_application_id,
        object_type=object_type,
        expected_count=len(expected),
        actual_count=len(actual),
        drifted=bool(drifts),
        drifts=tuple(drifts),
    )


class IngestReconcileService:
    async def load_change_log(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
    ) -> list[ChangeLogEntry]:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT object_id, operation, version, payload,
                           payload_contract_version, content_hash
                    FROM platform_raw.raw_change_record
                    WHERE source_application_id = :source_application_id
                      AND object_type = :object_type
                    ORDER BY version ASC, id ASC
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                },
            )
        ).mappings()
        entries: list[ChangeLogEntry] = []
        for row in rows:
            payload = row["payload"]
            entries.append(
                ChangeLogEntry(
                    object_id=str(row["object_id"]),
                    operation=row["operation"],
                    version=int(row["version"]),
                    payload=None if payload is None else dict(payload),
                    payload_contract_version=(
                        None
                        if row["payload_contract_version"] is None
                        else str(row["payload_contract_version"])
                    ),
                    content_hash=(
                        None if row["content_hash"] is None else str(row["content_hash"])
                    ),
                )
            )
        return entries

    async def load_current_state(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
    ) -> dict[str, StateFingerprint]:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT object_id, version, payload, payload_contract_version
                    FROM platform_raw.raw_current_state
                    WHERE source_application_id = :source_application_id
                      AND object_type = :object_type
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                },
            )
        ).mappings()
        actual: dict[str, StateFingerprint] = {}
        for row in rows:
            payload = None if row["payload"] is None else dict(row["payload"])
            object_id = str(row["object_id"])
            actual[object_id] = StateFingerprint(
                object_id=object_id,
                version=int(row["version"]),
                content_hash=payload_content_hash(payload),
                payload_contract_version=(
                    None
                    if row["payload_contract_version"] is None
                    else str(row["payload_contract_version"])
                ),
                payload=payload,
            )
        return actual

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
    ) -> ReconcileReport:
        entries = await self.load_change_log(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
        )
        expected = replay_change_log(entries)
        actual = await self.load_current_state(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
        )
        return compare_current_state(
            source_application_id=source_application_id,
            object_type=object_type,
            expected=expected,
            actual=actual,
        )

    async def rebuild_current_state_from_log(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
    ) -> dict[str, Any]:
        """Replace current-state rows by replaying the authoritative change log."""
        entries = await self.load_change_log(
            session,
            source_application_id=source_application_id,
            object_type=object_type,
        )
        expected = replay_change_log(entries)
        await session.execute(
            text(
                """
                DELETE FROM platform_raw.raw_current_state
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
            },
        )
        for fingerprint in expected.values():
            payload = None if fingerprint.payload is None else dict(fingerprint.payload)
            await session.execute(
                text(
                    """
                    INSERT INTO platform_raw.raw_current_state (
                        source_application_id, object_type, object_id,
                        payload, version, payload_contract_version, updated_at
                    ) VALUES (
                        :source_application_id, :object_type, :object_id,
                        CAST(:payload AS jsonb), :version,
                        :payload_contract_version, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "object_id": fingerprint.object_id,
                    "payload": None if payload is None else json.dumps(payload, ensure_ascii=True),
                    "version": fingerprint.version,
                    "payload_contract_version": fingerprint.payload_contract_version,
                },
            )
        return {
            "source_application_id": source_application_id,
            "object_type": object_type,
            "mode": "log",
            "rebuilt_count": len(expected),
            "change_log_entries": len(entries),
        }


async def reconcile_source(
    sessions: async_sessionmaker[AsyncSession],
    *,
    source_application_id: str,
    object_type: str,
) -> ReconcileReport:
    service = IngestReconcileService()
    async with sessions() as session:
        async with session.begin():
            return await service.reconcile(
                session,
                source_application_id=source_application_id,
                object_type=object_type,
            )


async def rebuild_from_log(
    sessions: async_sessionmaker[AsyncSession],
    *,
    source_application_id: str,
    object_type: str,
) -> dict[str, Any]:
    service = IngestReconcileService()
    async with sessions() as session:
        async with session.begin():
            return await service.rebuild_current_state_from_log(
                session,
                source_application_id=source_application_id,
                object_type=object_type,
            )


def ingest_records_from_replay(entries: Sequence[ChangeLogEntry]) -> list[IngestRecord]:
    expected = replay_change_log(entries)
    return [
        IngestRecord(
            object_id=fingerprint.object_id,
            operation="upsert",
            version=fingerprint.version,
            payload=None if fingerprint.payload is None else dict(fingerprint.payload),
        )
        for fingerprint in sorted(expected.values(), key=lambda row: row.object_id)
    ]


async def prune_change_records(
    sessions: async_sessionmaker[AsyncSession],
    *,
    keep_versions: int,
    keep_days: int | None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Prune raw_change_record beyond the retention policy (design §8).

    Keeps, per (source_application_id, object_type, object_id), the newest
    ``keep_versions`` rows and, when ``keep_days`` is set, any row received
    within that many days. ``dry_run`` only counts what would be deleted.
    """
    if keep_versions < 1:
        raise ValueError("keep_versions must be >= 1")
    if keep_days is not None and keep_days < 1:
        raise ValueError("keep_days must be >= 1 when set")

    ranked = """
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY source_application_id, object_type, object_id
                   ORDER BY version DESC
               ) AS version_rank,
               received_at
        FROM platform_raw.raw_change_record
    """
    age_clause = (
        "AND received_at < CURRENT_TIMESTAMP - (:keep_days || ' days')::interval"
        if keep_days is not None
        else ""
    )
    candidate_sql = f"""
        SELECT id FROM ({ranked}) AS ranked
        WHERE version_rank > :keep_versions
        {age_clause}
    """
    params: dict[str, Any] = {"keep_versions": keep_versions}
    if keep_days is not None:
        params["keep_days"] = str(keep_days)

    async with sessions() as session:
        async with session.begin():
            count_row = await session.execute(
                text(f"SELECT count(*) FROM ({candidate_sql}) AS c"), params
            )
            candidates = int(count_row.scalar_one())
            deleted = 0
            if not dry_run and candidates:
                deleted_rows = await session.execute(
                    text(
                        f"""
                        DELETE FROM platform_raw.raw_change_record
                        WHERE id IN ({candidate_sql})
                        RETURNING id
                        """
                    ),
                    params,
                )
                deleted = len(deleted_rows.all())
            if dry_run:
                await session.rollback()
            return {
                "dry_run": dry_run,
                "keep_versions": keep_versions,
                "keep_days": keep_days,
                "candidates": int(candidates),
                "deleted": deleted,
            }
