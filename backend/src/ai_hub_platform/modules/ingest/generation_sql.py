"""Durable PUSH_AGENT generation store backed by platform_raw."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_hub_platform.modules.ingest.config_store import IngestConfigStore
from ai_hub_platform.modules.ingest.generation import (
    PUSH_MAX_GENERATION_LIFETIME,
    STAGING_RETENTION,
    TERMINAL_GENERATION_STATUSES,
    AcceptedBatch,
    GenerationPurpose,
    GenerationState,
    GenerationStatus,
    PushIngestError,
    SourceBatchReceipt,
    StagingRecord,
)
from ai_hub_platform.modules.ingest.service import (
    IngestRecord,
    IngestService,
    payload_content_hash,
)
from ai_hub_platform.modules.ingest.source_lock import lock_ingest_source

LOGGER = logging.getLogger(__name__)
LEASE_REAPER_INTERVAL_SECONDS = 30

_GENERATION_COLUMNS = """
    generation_id, source_application_id, object_type, external_generation_id,
    request_digest, sync_mode, status, next_sequence_no,
    client_lease_expires_at, worker_lease_expires_at, completion_digest,
    accepted_batches, final_receipt, error_code, payload_contract_version,
    schema_fingerprint, completion_request, lock_version, purpose, created_at
"""


def _lock_suffix(for_update: bool) -> str:
    return " FOR UPDATE" if for_update else ""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an int")
    return value


def _json_str(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("optional field must be a string")
    return value


def _batches_from_json(raw: object) -> list[AcceptedBatch]:
    if not isinstance(raw, list):
        return []
    batches: list[AcceptedBatch] = []
    for raw_item in cast(list[object], raw):
        if not isinstance(raw_item, dict):
            continue
        payload = cast(dict[str, object], raw_item)
        fingerprint = _optional_str(payload.get("schema_fingerprint"))
        raw_batch_id = _optional_str(payload.get("raw_batch_id"))
        raw_bytes = payload.get("content_bytes", 0)
        batches.append(
            AcceptedBatch(
                sequence_no=_json_int(payload.get("sequence_no"), "sequence_no"),
                external_batch_id=_json_str(
                    payload.get("external_batch_id"), "external_batch_id"
                ),
                content_sha256=_json_str(
                    payload.get("content_sha256"), "content_sha256"
                ),
                record_count=_json_int(payload.get("record_count"), "record_count"),
                high_watermark=_json_int(payload.get("high_watermark"), "high_watermark"),
                payload_contract_version=_json_str(
                    payload.get("payload_contract_version"),
                    "payload_contract_version",
                ),
                schema_fingerprint=fingerprint,
                raw_batch_id=raw_batch_id,
                content_bytes=0 if raw_bytes is None else _json_int(raw_bytes, "content_bytes"),
            )
        )
    return batches


def _row_to_generation(row: Any) -> GenerationState:
    receipt = row.final_receipt
    return GenerationState(
        generation_id=row.generation_id
        if isinstance(row.generation_id, UUID)
        else UUID(str(row.generation_id)),
        source_application_id=str(row.source_application_id),
        object_type=str(row.object_type),
        external_generation_id=str(row.external_generation_id),
        request_digest=str(row.request_digest),
        sync_mode=cast(Literal["full", "incremental"], str(row.sync_mode)),
        status=cast(GenerationStatus, str(row.status)),
        next_sequence_no=int(row.next_sequence_no),
        client_lease_expires_at=_as_utc(row.client_lease_expires_at),
        worker_lease_expires_at=(
            None
            if row.worker_lease_expires_at is None
            else _as_utc(row.worker_lease_expires_at)
        ),
        completion_digest=(
            None if row.completion_digest is None else str(row.completion_digest)
        ),
        accepted_batches=_batches_from_json(row.accepted_batches),
        final_receipt=None if receipt is None else dict(receipt),
        error_code=None if row.error_code is None else str(row.error_code),
        payload_contract_version=(
            None
            if row.payload_contract_version is None
            else str(row.payload_contract_version)
        ),
        schema_fingerprint=(
            None if row.schema_fingerprint is None else str(row.schema_fingerprint)
        ),
        completion_request=_completion_request_from_json(row.completion_request),
        lock_version=int(row.lock_version),
        purpose=cast(
            Literal["production", "certification"],
            str(getattr(row, "purpose", None) or "production"),
        ),
        created_at=None if getattr(row, "created_at", None) is None else _as_utc(row.created_at),
    )


class SqlGenerationStore:
    """Persist generations, staging, and IngestService loads in one Raw transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._ingest = IngestService()

    async def lock_source(
        self, source_application_id: str, object_type: str
    ) -> None:
        await lock_ingest_source(self.session, source_application_id, object_type)

    async def get(
        self, generation_id: UUID, *, for_update: bool = False
    ) -> GenerationState | None:
        result = await self.session.execute(
            text(
                f"""
                SELECT {_GENERATION_COLUMNS}
                FROM platform_raw.raw_push_generation
                WHERE generation_id = :generation_id
                {_lock_suffix(for_update)}
                """
            ),
            {"generation_id": generation_id},
        )
        row = result.one_or_none()
        return None if row is None else _row_to_generation(row)

    async def get_by_external(
        self,
        source_application_id: str,
        object_type: str,
        external_generation_id: str,
        *,
        for_update: bool = False,
    ) -> GenerationState | None:
        result = await self.session.execute(
            text(
                f"""
                SELECT {_GENERATION_COLUMNS}
                FROM platform_raw.raw_push_generation
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND external_generation_id = :external_generation_id
                {_lock_suffix(for_update)}
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "external_generation_id": external_generation_id,
            },
        )
        row = result.one_or_none()
        return None if row is None else _row_to_generation(row)

    async def get_active(
        self,
        source_application_id: str,
        object_type: str,
        *,
        for_update: bool = False,
    ) -> GenerationState | None:
        result = await self.session.execute(
            text(
                f"""
                SELECT {_GENERATION_COLUMNS}
                FROM platform_raw.raw_push_generation
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND status IN ('OPEN', 'RECEIVING', 'COMPLETING')
                {_lock_suffix(for_update)}
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
            },
        )
        row = result.one_or_none()
        return None if row is None else _row_to_generation(row)

    async def insert(
        self,
        generation: GenerationState,
        *,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None:
        try:
            await self.session.execute(
                text(
                    """
                    INSERT INTO platform_raw.raw_push_generation (
                        generation_id, source_application_id, object_type,
                        external_generation_id, request_digest, sync_mode, status,
                        next_sequence_no, client_lease_expires_at,
                        worker_lease_expires_at, completion_digest, accepted_batches,
                        final_receipt, error_code, payload_contract_version,
                        schema_fingerprint, completion_request, lock_version, purpose
                    ) VALUES (
                        :generation_id, :source_application_id, :object_type,
                        :external_generation_id, :request_digest, :sync_mode, :status,
                        :next_sequence_no, :client_lease_expires_at,
                        :worker_lease_expires_at, :completion_digest,
                        CAST(:accepted_batches AS jsonb),
                        CAST(:final_receipt AS jsonb), :error_code,
                        :payload_contract_version, :schema_fingerprint,
                        CAST(:completion_request AS jsonb), :lock_version, :purpose
                    )
                    """
                ),
                _generation_params(generation),
            )
        except IntegrityError as error:
            raise _insert_conflict(error) from error
        await self._record_transition(
            generation.generation_id,
            None,
            generation.status,
            "create",
            actor=actor,
            request_id=request_id,
        )

    async def save(
        self,
        generation: GenerationState,
        *,
        transition_reason: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None:
        next_lock = generation.lock_version + 1
        await self.session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_push_generation_transition (
                    transition_id, generation_id, from_status, to_status, reason,
                    actor, request_id
                )
                SELECT :transition_id, generation_id, status, :to_status,
                       CASE
                         WHEN CAST(:force_transition AS boolean)
                              THEN :transition_reason
                         WHEN status IS DISTINCT FROM :to_status
                              AND :to_status = 'EXPIRED' THEN 'client_lease_expired'
                         WHEN status IS DISTINCT FROM :to_status
                              AND :to_status = 'FAILED' THEN 'completion_failed'
                         WHEN status = 'COMPLETING' AND :to_status = 'COMPLETED'
                              THEN 'publish'
                         WHEN status IS DISTINCT FROM :to_status
                              AND :to_status = 'COMPLETING' THEN 'complete'
                         WHEN status IS DISTINCT FROM :to_status
                              AND :to_status = 'ABORTED' THEN 'abort'
                         WHEN status = 'OPEN' AND :to_status = 'RECEIVING'
                              THEN 'first_batch'
                         ELSE NULL
                       END,
                       :actor,
                       :request_id
                FROM platform_raw.raw_push_generation
                WHERE generation_id = :generation_id
                  AND (
                    status IS DISTINCT FROM :to_status
                    OR CAST(:force_transition AS boolean)
                  )
                """
            ),
            {
                "transition_id": uuid4(),
                "generation_id": generation.generation_id,
                "to_status": generation.status,
                "force_transition": transition_reason is not None,
                "transition_reason": transition_reason,
                "actor": actor,
                "request_id": request_id,
            },
        )
        result = await self.session.execute(
            text(
                """
                UPDATE platform_raw.raw_push_generation
                SET status = :status,
                    next_sequence_no = :next_sequence_no,
                    client_lease_expires_at = :client_lease_expires_at,
                    worker_lease_expires_at = :worker_lease_expires_at,
                    completion_digest = :completion_digest,
                    accepted_batches = CAST(:accepted_batches AS jsonb),
                    final_receipt = CAST(:final_receipt AS jsonb),
                    error_code = :error_code,
                    payload_contract_version = :payload_contract_version,
                    schema_fingerprint = :schema_fingerprint,
                    completion_request = CAST(:completion_request AS jsonb),
                    lock_version = :next_lock,
                    updated_at = CURRENT_TIMESTAMP
                WHERE generation_id = :generation_id
                  AND lock_version = :lock_version
                """
            ),
            {
                **_generation_params(generation),
                "lock_version": generation.lock_version,
                "next_lock": next_lock,
            },
        )
        if int(getattr(result, "rowcount", 0) or 0) == 0:
            raise PushIngestError(
                "generation_conflict",
                "generation was updated concurrently",
            )
        generation.lock_version = next_lock

    async def _record_transition(
        self,
        generation_id: UUID,
        from_status: str | None,
        to_status: str,
        reason: str | None,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_push_generation_transition (
                    transition_id, generation_id, from_status, to_status, reason,
                    actor, request_id
                ) VALUES (
                    :transition_id, :generation_id, :from_status, :to_status, :reason,
                    :actor, :request_id
                )
                """
            ),
            {
                "transition_id": uuid4(),
                "generation_id": generation_id,
                "from_status": from_status,
                "to_status": to_status,
                "reason": reason,
                "actor": actor,
                "request_id": request_id,
            },
        )

    async def append_staging(
        self, generation_id: UUID, records: Sequence[StagingRecord]
    ) -> None:
        for item in records:
            try:
                await self.session.execute(
                    text(
                        """
                        INSERT INTO platform_raw.raw_push_staging (
                            generation_id, sequence_no, object_id, operation, version,
                            payload, payload_contract_version, content_hash
                        ) VALUES (
                            :generation_id, :sequence_no, :object_id, :operation, :version,
                            CAST(:payload AS jsonb), :payload_contract_version, :content_hash
                        )
                        """
                    ),
                    {
                        "generation_id": generation_id,
                        "sequence_no": item.sequence_no,
                        "object_id": item.record.object_id,
                        "operation": item.record.operation,
                        "version": item.record.version,
                        "payload": (
                            None
                            if item.record.payload is None
                            else json.dumps(dict(item.record.payload), sort_keys=True)
                        ),
                        "payload_contract_version": item.payload_contract_version,
                        "content_hash": payload_content_hash(item.record.payload),
                    },
                )
            except IntegrityError as error:
                raise PushIngestError(
                    "invalid_ingest_record",
                    "full batch repeats object_id in the same sequence",
                    status_code=400,
                ) from error

    async def list_staging(self, generation_id: UUID) -> list[StagingRecord]:
        result = await self.session.execute(
            text(
                """
                SELECT sequence_no, object_id, operation, version, payload,
                       payload_contract_version
                FROM platform_raw.raw_push_staging
                WHERE generation_id = :generation_id
                ORDER BY sequence_no, object_id
                """
            ),
            {"generation_id": generation_id},
        )
        staged: list[StagingRecord] = []
        for row in result.all():
            payload = None if row.payload is None else dict(row.payload)
            staged.append(
                StagingRecord(
                    sequence_no=int(row.sequence_no),
                    record=IngestRecord(
                        str(row.object_id),
                        cast(Literal["upsert", "delete"], str(row.operation)),
                        int(row.version),
                        payload,
                    ),
                    payload_contract_version=str(row.payload_contract_version),
                )
            )
        return staged

    async def get_source_batch(
        self,
        source_application_id: str,
        object_type: str,
        external_batch_id: str,
        *,
        purpose: GenerationPurpose = "production",
    ) -> SourceBatchReceipt | None:
        result = await self.session.execute(
            text(
                """
                SELECT generation_id, sequence_no, external_batch_id, content_sha256,
                       record_count, high_watermark, payload_contract_version,
                       schema_fingerprint, raw_batch_id
                FROM platform_raw.raw_push_batch_receipt
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                  AND external_batch_id = :external_batch_id
                  AND purpose = :purpose
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "external_batch_id": external_batch_id,
                "purpose": purpose,
            },
        )
        row = result.one_or_none()
        if row is None:
            return None
        return SourceBatchReceipt(
            generation_id=(
                row.generation_id
                if isinstance(row.generation_id, UUID)
                else UUID(str(row.generation_id))
            ),
            batch=AcceptedBatch(
                sequence_no=int(row.sequence_no),
                external_batch_id=str(row.external_batch_id),
                content_sha256=str(row.content_sha256),
                record_count=int(row.record_count),
                high_watermark=int(row.high_watermark),
                payload_contract_version=str(row.payload_contract_version),
                schema_fingerprint=(
                    None
                    if row.schema_fingerprint is None
                    else str(row.schema_fingerprint)
                ),
                raw_batch_id=None if row.raw_batch_id is None else str(row.raw_batch_id),
            ),
        )

    async def put_source_batch(
        self,
        source_application_id: str,
        object_type: str,
        generation_id: UUID,
        batch: AcceptedBatch,
        *,
        purpose: GenerationPurpose = "production",
    ) -> None:
        existing = await self.get_source_batch(
            source_application_id, object_type, batch.external_batch_id, purpose=purpose
        )
        if existing is not None:
            if existing.batch.content_sha256 != batch.content_sha256:
                raise PushIngestError(
                    "batch_digest_conflict",
                    "external_batch_id was reused with a different content digest",
                )
            if existing.batch.raw_batch_id is not None:
                return
            await self.session.execute(
                text(
                    """
                    UPDATE platform_raw.raw_push_batch_receipt
                    SET generation_id = :generation_id,
                        sequence_no = :sequence_no,
                        record_count = :record_count,
                        high_watermark = :high_watermark,
                        payload_contract_version = :payload_contract_version,
                        schema_fingerprint = :schema_fingerprint,
                        raw_batch_id = :raw_batch_id
                    WHERE source_application_id = :source_application_id
                      AND object_type = :object_type
                      AND external_batch_id = :external_batch_id
                      AND purpose = :purpose
                      AND raw_batch_id IS NULL
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "external_batch_id": batch.external_batch_id,
                    "generation_id": generation_id,
                    "sequence_no": batch.sequence_no,
                    "record_count": batch.record_count,
                    "high_watermark": batch.high_watermark,
                    "payload_contract_version": batch.payload_contract_version,
                    "schema_fingerprint": batch.schema_fingerprint,
                    "raw_batch_id": batch.raw_batch_id,
                    "purpose": purpose,
                },
            )
            return
        try:
            await self.session.execute(
                text(
                    """
                    INSERT INTO platform_raw.raw_push_batch_receipt (
                        source_application_id, object_type, external_batch_id,
                        generation_id, sequence_no, content_sha256, record_count,
                        high_watermark, payload_contract_version, schema_fingerprint,
                        raw_batch_id, purpose
                    ) VALUES (
                        :source_application_id, :object_type, :external_batch_id,
                        :generation_id, :sequence_no, :content_sha256, :record_count,
                        :high_watermark, :payload_contract_version, :schema_fingerprint,
                        :raw_batch_id, :purpose
                    )
                    """
                ),
                {
                    "source_application_id": source_application_id,
                    "object_type": object_type,
                    "external_batch_id": batch.external_batch_id,
                    "generation_id": generation_id,
                    "sequence_no": batch.sequence_no,
                    "content_sha256": batch.content_sha256,
                    "record_count": batch.record_count,
                    "high_watermark": batch.high_watermark,
                    "payload_contract_version": batch.payload_contract_version,
                    "schema_fingerprint": batch.schema_fingerprint,
                    "raw_batch_id": batch.raw_batch_id,
                    "purpose": purpose,
                },
            )
        except IntegrityError as error:
            raise PushIngestError(
                "batch_digest_conflict",
                "external_batch_id was reused with a different content digest",
            ) from error

    async def load_incremental(
        self,
        *,
        source_application_id: str,
        object_type: str,
        records: Sequence[IngestRecord],
        high_watermark: int,
        payload_contract_version: str,
        generation_id: UUID | None = None,
        external_batch_id: str | None = None,
        content_sha256: str | None = None,
        schema_fingerprint: str | None = None,
        apply_current_state: bool = True,
        purpose: GenerationPurpose = "production",
    ) -> str | None:
        loaded = await self._ingest.load_batch(
            self.session,
            source_application_id=source_application_id,
            object_type=object_type,
            sync_mode="incremental",
            records=records,
            high_watermark=high_watermark,
            payload_contract_version=payload_contract_version,
            transport_mode="PUSH_AGENT",
            external_batch_id=external_batch_id,
            generation_id=generation_id,
            content_sha256=content_sha256,
            schema_fingerprint=schema_fingerprint,
            apply_current_state=apply_current_state,
            purpose=purpose,
        )
        return str(loaded.batch_id)

    async def publish_full(
        self,
        *,
        source_application_id: str,
        object_type: str,
        records: Sequence[IngestRecord],
        high_watermark: int,
        payload_contract_version: str,
        generation_id: UUID | None = None,
        schema_fingerprint: str | None = None,
        apply_current_state: bool = True,
        purpose: GenerationPurpose = "production",
    ) -> dict[str, Any]:
        loaded = await self._ingest.load_batch(
            self.session,
            source_application_id=source_application_id,
            object_type=object_type,
            sync_mode="full",
            records=records,
            high_watermark=high_watermark,
            payload_contract_version=payload_contract_version,
            transport_mode="PUSH_AGENT",
            generation_id=generation_id,
            schema_fingerprint=schema_fingerprint,
            apply_current_state=apply_current_state,
            purpose=purpose,
        )
        return {
            "record_count": len(records),
            "tombstones": loaded.tombstones,
            "high_watermark": high_watermark,
            "payload_contract_version": payload_contract_version,
            "raw_batch_id": str(loaded.batch_id),
        }

    async def list_lease_candidates(self, now: datetime) -> list[GenerationState]:
        result = await self.session.execute(
            text(
                f"""
                SELECT {_GENERATION_COLUMNS}
                FROM platform_raw.raw_push_generation
                WHERE status IN ('OPEN', 'RECEIVING')
                  AND (
                        client_lease_expires_at < :now
                        OR created_at <= :lifetime_cutoff
                  )
                """
            ),
            {
                "now": now,
                "lifetime_cutoff": now - PUSH_MAX_GENERATION_LIFETIME,
            },
        )
        return [_row_to_generation(row) for row in result.all()]

    async def list_completing_candidates(self, now: datetime) -> list[GenerationState]:
        result = await self.session.execute(
            text(
                f"""
                SELECT {_GENERATION_COLUMNS}
                FROM platform_raw.raw_push_generation
                WHERE status = 'COMPLETING'
                  AND worker_lease_expires_at IS NOT NULL
                  AND worker_lease_expires_at < :now
                """
            ),
            {"now": now},
        )
        return [_row_to_generation(row) for row in result.all()]

    async def get_committed_watermark(
        self, source_application_id: str, object_type: str
    ) -> int:
        result = await self.session.execute(
            text(
                """
                SELECT high_watermark
                FROM platform_raw.raw_push_committed_watermark
                WHERE source_application_id = :source_application_id
                  AND object_type = :object_type
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
            },
        )
        row = result.one_or_none()
        return 0 if row is None else int(row.high_watermark)

    async def put_committed_watermark(
        self,
        source_application_id: str,
        object_type: str,
        high_watermark: int,
        generation_id: UUID,
    ) -> None:
        await self.session.execute(
            text(
                """
                INSERT INTO platform_raw.raw_push_committed_watermark (
                    source_application_id, object_type, high_watermark, generation_id
                ) VALUES (
                    :source_application_id, :object_type, :high_watermark,
                    :generation_id
                )
                ON CONFLICT (source_application_id, object_type) DO UPDATE
                SET high_watermark = EXCLUDED.high_watermark,
                    generation_id = EXCLUDED.generation_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE platform_raw.raw_push_committed_watermark.high_watermark
                      <= EXCLUDED.high_watermark
                """
            ),
            {
                "source_application_id": source_application_id,
                "object_type": object_type,
                "high_watermark": high_watermark,
                "generation_id": generation_id,
            },
        )

    async def purge_terminal_staging(self, cutoff: datetime) -> int:
        listed = ", ".join(
            f"'{status}'" for status in sorted(TERMINAL_GENERATION_STATUSES)
        )
        result = await self.session.execute(
            text(
                f"""
                DELETE FROM platform_raw.raw_push_staging AS staging
                USING platform_raw.raw_push_generation AS generation
                WHERE staging.generation_id = generation.generation_id
                  AND generation.status IN ({listed})
                  AND generation.updated_at < :cutoff
                """
            ),
            {"cutoff": cutoff},
        )
        return int(getattr(result, "rowcount", 0) or 0)


def _generation_params(generation: GenerationState) -> dict[str, Any]:
    receipt = None if generation.final_receipt is None else json.dumps(generation.final_receipt)
    return {
        "generation_id": generation.generation_id,
        "source_application_id": generation.source_application_id,
        "object_type": generation.object_type,
        "external_generation_id": generation.external_generation_id,
        "request_digest": generation.request_digest,
        "sync_mode": generation.sync_mode,
        "status": generation.status,
        "next_sequence_no": generation.next_sequence_no,
        "client_lease_expires_at": generation.client_lease_expires_at,
        "worker_lease_expires_at": generation.worker_lease_expires_at,
        "completion_digest": generation.completion_digest,
        "accepted_batches": json.dumps(
            [batch.as_dict() for batch in generation.accepted_batches]
        ),
        "final_receipt": receipt,
        "error_code": generation.error_code,
        "payload_contract_version": generation.payload_contract_version,
        "schema_fingerprint": generation.schema_fingerprint,
        "completion_request": (
            None
            if generation.completion_request is None
            else json.dumps(generation.completion_request)
        ),
        "lock_version": generation.lock_version,
        "purpose": generation.purpose,
    }


def _completion_request_from_json(raw: object) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    return dict(cast(dict[str, Any], raw))


def _insert_conflict(error: IntegrityError) -> PushIngestError:
    detail = str(getattr(error, "orig", error)).lower()
    if "uq_raw_push_generation_one_active" in detail:
        return PushIngestError(
            "generation_in_progress",
            "an active generation already exists for this source/object",
        )
    if "uq_raw_push_generation_external_id" in detail:
        return PushIngestError(
            "generation_digest_conflict",
            "external_generation_id already exists with a different request digest",
        )
    return PushIngestError(
        "generation_in_progress",
        "an active generation already exists for this source/object",
    )


async def reap_expired_push_generations(
    sessions: async_sessionmaker[AsyncSession],
) -> list[UUID]:
    from ai_hub_platform.modules.ingest.generation import PushGenerationService

    async with sessions() as session:
        async with session.begin():
            store = SqlGenerationStore(session)
            now = datetime.now(tz=UTC)
            expire_ids = [
                candidate.generation_id
                for candidate in await store.list_lease_candidates(now)
            ]
            recover_ids = [
                candidate.generation_id
                for candidate in await store.list_completing_candidates(now)
            ]
    handled: list[UUID] = []
    for generation_id in expire_ids:
        try:
            async with sessions() as session:
                async with session.begin():
                    service = PushGenerationService(SqlGenerationStore(session))
                    result = await service.expire_one(generation_id)
                    if result is not None:
                        handled.append(result)
        except Exception:
            LOGGER.exception(
                "push generation expire failed generation_id=%s", generation_id
            )
    for generation_id in recover_ids:
        try:
            async with sessions() as session:
                async with session.begin():
                    service = PushGenerationService(SqlGenerationStore(session))
                    result = await service.recover_one(generation_id)
                    if result is not None:
                        handled.append(result)
        except Exception:
            LOGGER.exception(
                "push generation recover failed generation_id=%s", generation_id
            )
    try:
        async with sessions() as session:
            async with session.begin():
                retention = STAGING_RETENTION
                try:
                    policy = await IngestConfigStore().get_policy(session)
                    retention = timedelta(hours=policy.push_staging_retention_hours)
                except Exception:
                    LOGGER.exception("push generation staging retention lookup failed")
                await PushGenerationService(
                    SqlGenerationStore(session)
                ).purge_stale_staging(retention=retention)
    except Exception:
        LOGGER.exception("push generation staging purge failed")
    return handled


def start_push_lease_reaper(
    sessions: async_sessionmaker[AsyncSession],
) -> asyncio.Task[None]:
    async def _loop() -> None:
        while True:
            try:
                await reap_expired_push_generations(sessions)
            except Exception:
                LOGGER.exception("push generation lease reaper failed")
            await asyncio.sleep(LEASE_REAPER_INTERVAL_SECONDS)

    return asyncio.create_task(_loop())
