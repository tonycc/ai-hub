"""Incremental raw ingest: batch load, idempotent log, and current-state maintenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SyncMode = Literal["full", "incremental"]
Operation = Literal["upsert", "delete"]


class IngestValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IngestRecord:
    object_id: str
    operation: Operation
    version: int
    payload: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LoadBatchResult:
    batch_id: UUID
    sync_mode: SyncMode
    records_accepted: int
    records_idempotent_skipped: int
    current_state_upserts: int
    current_state_deletes: int
    tombstones: int
    high_watermark: int


def payload_content_hash(payload: Mapping[str, Any] | None) -> str:
    if payload is None:
        return hashlib.sha256(b"").hexdigest()
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def should_apply_version(incoming: int, existing: int | None) -> bool:
    return existing is None or incoming > existing


def tombstone_version(current_version: int, high_watermark: int) -> int:
    return max(current_version + 1, high_watermark)


def validate_ingest_records(
    records: Sequence[IngestRecord],
    *,
    high_watermark: int,
) -> None:
    if high_watermark < 0:
        raise IngestValidationError("high_watermark must be >= 0")
    seen: set[tuple[str, int]] = set()
    max_version = 0
    for record in records:
        if not record.object_id.strip():
            raise IngestValidationError("object_id cannot be empty")
        if record.version < 1:
            raise IngestValidationError("version must be >= 1")
        if record.operation == "upsert" and record.payload is None:
            raise IngestValidationError("upsert records require a payload")
        if record.operation == "delete" and record.payload is not None:
            raise IngestValidationError("delete records must have a null payload")
        key = (record.object_id, record.version)
        if key in seen:
            raise IngestValidationError(
                f"duplicate object_id/version in batch: {record.object_id}@{record.version}"
            )
        seen.add(key)
        max_version = max(max_version, record.version)
    if records and high_watermark < max_version:
        raise IngestValidationError(
            "high_watermark must be >= the maximum record version in the batch"
        )


class IngestService:
    """Load export batches into platform_raw within the caller's transaction."""

    async def load_batch(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        sync_mode: SyncMode,
        records: Sequence[IngestRecord],
        high_watermark: int,
        payload_contract_version: str,
        from_version: int | None = None,
    ) -> LoadBatchResult:
        if not source_application_id.strip():
            raise IngestValidationError("source_application_id cannot be empty")
        if not object_type.strip():
            raise IngestValidationError("object_type cannot be empty")
        if sync_mode not in {"full", "incremental"}:
            raise IngestValidationError("sync_mode must be full or incremental")
        if not payload_contract_version.strip():
            raise IngestValidationError("payload_contract_version cannot be empty")
        validate_ingest_records(records, high_watermark=high_watermark)

        batch_id = uuid4()
        started_at = datetime.now(UTC)
        await session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_ingest_batch (
                    batch_id, source_application_id, object_type, sync_mode,
                    from_version, to_version, record_count, status, started_at
                ) VALUES (
                    :batch_id, :source_application_id, :object_type, :sync_mode,
                    :from_version, :to_version, :record_count, 'running', :started_at
                )
                """
            ),
            {
                "batch_id": batch_id,
                "source_application_id": source_application_id,
                "object_type": object_type,
                "sync_mode": sync_mode,
                "from_version": from_version,
                "to_version": high_watermark,
                "record_count": len(records),
                "started_at": started_at,
            },
        )

        accepted = 0
        skipped = 0
        upserts = 0
        deletes = 0
        for record in records:
            inserted = await self._insert_change_record(
                session,
                batch_id=batch_id,
                source_application_id=source_application_id,
                object_type=object_type,
                record=record,
                payload_contract_version=payload_contract_version,
            )
            if inserted:
                accepted += 1
            else:
                skipped += 1
            applied = await self._apply_current_state(
                session,
                source_application_id=source_application_id,
                object_type=object_type,
                record=record,
                payload_contract_version=payload_contract_version,
                bypass_version_check=False,
            )
            if applied == "upsert":
                upserts += 1
            elif applied == "delete":
                deletes += 1

        tombstones = 0
        if sync_mode == "full":
            tombstones = await self._synthesize_full_tombstones(
                session,
                batch_id=batch_id,
                source_application_id=source_application_id,
                object_type=object_type,
                exported_object_ids={record.object_id for record in records},
                high_watermark=high_watermark,
                payload_contract_version=payload_contract_version,
            )
            deletes += tombstones

        await session.execute(
            text(
                """
                UPDATE platform_raw.raw_ingest_batch
                SET status = 'loaded',
                    finished_at = :finished_at,
                    record_count = :record_count,
                    to_version = :to_version,
                    error = NULL
                WHERE batch_id = :batch_id
                """
            ),
            {
                "batch_id": batch_id,
                "finished_at": datetime.now(UTC),
                "record_count": len(records),
                "to_version": high_watermark,
            },
        )

        return LoadBatchResult(
            batch_id=batch_id,
            sync_mode=sync_mode,
            records_accepted=accepted,
            records_idempotent_skipped=skipped,
            current_state_upserts=upserts,
            current_state_deletes=deletes,
            tombstones=tombstones,
            high_watermark=high_watermark,
        )

    async def advance_cursor(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        last_version: int,
        status: Literal["ok", "failed"] = "ok",
    ) -> None:
        if last_version < 0:
            raise IngestValidationError("last_version must be >= 0")
        await session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_sync_cursor (
                    source_application_id, object_type, last_version,
                    last_synced_at, last_status
                ) VALUES (
                    :source_application_id, :object_type, :last_version,
                    :last_synced_at, :last_status
                )
                ON CONFLICT (source_application_id, object_type) DO UPDATE
                SET last_version = EXCLUDED.last_version,
                    last_synced_at = EXCLUDED.last_synced_at,
                    last_status = EXCLUDED.last_status
                WHERE platform_raw.raw_sync_cursor.last_version <= EXCLUDED.last_version
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "last_version": last_version,
                "last_synced_at": datetime.now(UTC),
                "last_status": status,
            },
        )

    async def get_cursor(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
    ) -> int:
        result = await session.execute(
            text(
                """
                SELECT last_version
                FROM platform_raw.raw_sync_cursor
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
            },
        )
        value = result.scalar_one_or_none()
        return int(value) if value is not None else 0

    async def record_failed_batch(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        sync_mode: SyncMode,
        from_version: int | None,
        error: str,
    ) -> UUID:
        batch_id = uuid4()
        now = datetime.now(UTC)
        await session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_ingest_batch (
                    batch_id, source_application_id, object_type, sync_mode,
                    from_version, to_version, record_count, status,
                    started_at, finished_at, error
                ) VALUES (
                    :batch_id, :source_application_id, :object_type, :sync_mode,
                    :from_version, NULL, 0, 'failed',
                    :started_at, :finished_at, :error
                )
                """
            ),
            {
                "batch_id": batch_id,
                "source_application_id": source_application_id,
                "object_type": object_type,
                "sync_mode": sync_mode,
                "from_version": from_version,
                "started_at": now,
                "finished_at": now,
                "error": error[:4000],
            },
        )
        return batch_id

    async def _insert_change_record(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        source_application_id: str,
        object_type: str,
        record: IngestRecord,
        payload_contract_version: str,
    ) -> bool:
        payload = dict(record.payload) if record.payload is not None else None
        result = await session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_change_record (
                    batch_id, source_application_id, object_type, object_id,
                    operation, version, payload, payload_contract_version, content_hash
                ) VALUES (
                    :batch_id, :source_application_id, :object_type, :object_id,
                    :operation, :version, CAST(:payload AS jsonb),
                    :payload_contract_version, :content_hash
                )
                ON CONFLICT (source_application_id, object_type, object_id, version)
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "batch_id": batch_id,
                "source_application_id": source_application_id,
                "object_type": object_type,
                "object_id": record.object_id,
                "operation": record.operation,
                "version": record.version,
                "payload": None if payload is None else json.dumps(payload, ensure_ascii=True),
                "payload_contract_version": payload_contract_version,
                "content_hash": payload_content_hash(payload),
            },
        )
        return result.scalar_one_or_none() is not None

    async def _apply_current_state(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
        record: IngestRecord,
        payload_contract_version: str,
        bypass_version_check: bool,
    ) -> Literal["upsert", "delete", "skipped"]:
        existing = await session.execute(
            text(
                """
                SELECT version
                FROM platform_raw.raw_current_state
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND object_id = :object_id
                FOR UPDATE
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "object_id": record.object_id,
            },
        )
        existing_version = existing.scalar_one_or_none()
        if not bypass_version_check and not should_apply_version(
            record.version,
            int(existing_version) if existing_version is not None else None,
        ):
            return "skipped"

        if record.operation == "delete":
            await session.execute(
                text(
                    """
                    DELETE FROM platform_raw.raw_current_state
                    WHERE source_application_id = :source_application_id
                      AND object_type = :object_type
                      AND object_id = :object_id
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "object_id": record.object_id,
                },
            )
            return "delete"

        payload = dict(record.payload) if record.payload is not None else {}
        await session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_current_state (
                    source_application_id, object_type, object_id,
                    payload, version, payload_contract_version, updated_at
                ) VALUES (
                    :source_application_id, :object_type, :object_id,
                    CAST(:payload AS jsonb), :version, :payload_contract_version, :updated_at
                )
                ON CONFLICT (source_application_id, object_type, object_id) DO UPDATE
                SET payload = EXCLUDED.payload,
                    version = EXCLUDED.version,
                    payload_contract_version = EXCLUDED.payload_contract_version,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "object_id": record.object_id,
                "payload": json.dumps(payload, ensure_ascii=True),
                "version": record.version,
                "payload_contract_version": payload_contract_version,
                "updated_at": datetime.now(UTC),
            },
        )
        return "upsert"

    async def _synthesize_full_tombstones(
        self,
        session: AsyncSession,
        *,
        batch_id: UUID,
        source_application_id: str,
        object_type: str,
        exported_object_ids: set[str],
        high_watermark: int,
        payload_contract_version: str,
    ) -> int:
        result = await session.execute(
            text(
                """
                SELECT object_id, version
                FROM platform_raw.raw_current_state
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                FOR UPDATE
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
            },
        )
        rows = result.all()
        tombstones = 0
        for object_id, current_version in rows:
            if object_id in exported_object_ids:
                continue
            version = tombstone_version(int(current_version), high_watermark)
            record = IngestRecord(
                object_id=str(object_id),
                operation="delete",
                version=version,
                payload=None,
            )
            await self._insert_change_record(
                session,
                batch_id=batch_id,
                source_application_id=source_application_id,
                object_type=object_type,
                record=record,
                payload_contract_version=payload_contract_version,
            )
            # Full snapshot absence is authoritative: bypass version compare.
            await self._apply_current_state(
                session,
                source_application_id=source_application_id,
                object_type=object_type,
                record=record,
                payload_contract_version=payload_contract_version,
                bypass_version_check=True,
            )
            tombstones += 1
        return tombstones
