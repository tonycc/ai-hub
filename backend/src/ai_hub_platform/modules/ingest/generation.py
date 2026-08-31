"""PUSH_AGENT generation state machine, staging, and durable receipts."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from re import fullmatch
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from ai_hub_platform.modules.ingest.contract import (
    ContractEnforcedError,
    IngestContractValidator,
    RegisteredContract,
    canonical_json_digest,
)
from ai_hub_platform.modules.ingest.service import (
    IngestRecord,
    IngestRecordConflictError,
    IngestValidationError,
    payload_content_hash,
    should_apply_version,
    validate_ingest_records,
)
from ai_hub_platform.modules.ingest.sources import (
    PUSH_PROTOCOL_VERSION,
    IngestSourceConfig,
)

GenerationStatus = Literal[
    "OPEN",
    "RECEIVING",
    "COMPLETING",
    "COMPLETED",
    "ABORTED",
    "EXPIRED",
    "FAILED",
]
GenerationPurpose = Literal["production", "certification"]
ACTIVE_GENERATION_STATUSES = frozenset({"OPEN", "RECEIVING", "COMPLETING"})
TERMINAL_GENERATION_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "ABORTED", "EXPIRED"}
)
CLIENT_LEASE = timedelta(seconds=60)
WORKER_LEASE = timedelta(seconds=120)
STAGING_RETENTION = timedelta(hours=24)
PUSH_MAX_BATCHES = 100
PUSH_MAX_GENERATION_ROWS = 500_000
PUSH_MAX_GENERATION_BYTES = 64 * 1024 * 1024
PUSH_MAX_GENERATION_LIFETIME = timedelta(hours=24)
_SHA256_HEX = r"[0-9a-f]{64}"

Clock = Callable[[], datetime]
LOGGER = logging.getLogger(__name__)


class PushIngestError(ValueError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.error_code = error_code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


@dataclass
class AcceptedBatch:
    sequence_no: int
    external_batch_id: str
    content_sha256: str
    record_count: int
    high_watermark: int
    payload_contract_version: str
    schema_fingerprint: str | None = None
    raw_batch_id: str | None = None
    content_bytes: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence_no": self.sequence_no,
            "external_batch_id": self.external_batch_id,
            "content_sha256": self.content_sha256,
            "record_count": self.record_count,
            "high_watermark": self.high_watermark,
            "payload_contract_version": self.payload_contract_version,
            "schema_fingerprint": self.schema_fingerprint,
            "raw_batch_id": self.raw_batch_id,
            "content_bytes": self.content_bytes,
        }


@dataclass(frozen=True, slots=True)
class SourceBatchReceipt:
    generation_id: UUID
    batch: AcceptedBatch


@dataclass(frozen=True, slots=True)
class GenerationTransition:
    generation_id: UUID
    from_status: str | None
    to_status: str
    reason: str | None = None
    actor: str | None = None
    request_id: str | None = None


@dataclass
class GenerationState:
    generation_id: UUID
    source_application_id: str
    object_type: str
    external_generation_id: str
    request_digest: str
    sync_mode: Literal["full", "incremental"]
    status: GenerationStatus
    next_sequence_no: int
    client_lease_expires_at: datetime
    worker_lease_expires_at: datetime | None = None
    completion_digest: str | None = None
    accepted_batches: list[AcceptedBatch] = field(default_factory=list[AcceptedBatch])
    final_receipt: dict[str, Any] | None = None
    error_code: str | None = None
    payload_contract_version: str | None = None
    schema_fingerprint: str | None = None
    completion_request: dict[str, Any] | None = None
    lock_version: int = 0
    purpose: GenerationPurpose = "production"
    created_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation_id": str(self.generation_id),
            "source_application_id": self.source_application_id,
            "object_type": self.object_type,
            "external_generation_id": self.external_generation_id,
            "sync_mode": self.sync_mode,
            "status": self.status,
            "purpose": self.purpose,
            "next_sequence_no": self.next_sequence_no,
            "client_lease_expires_at": self.client_lease_expires_at.isoformat(),
            "worker_lease_expires_at": (
                None
                if self.worker_lease_expires_at is None
                else self.worker_lease_expires_at.isoformat()
            ),
            "accepted_batch_count": len(self.accepted_batches),
            "payload_contract_version": self.payload_contract_version,
            "schema_fingerprint": self.schema_fingerprint,
            "final_receipt": self.final_receipt,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class StagingRecord:
    sequence_no: int
    record: IngestRecord
    payload_contract_version: str


class GenerationStore(Protocol):
    async def lock_source(
        self, source_application_id: str, object_type: str
    ) -> None: ...

    async def get(
        self, generation_id: UUID, *, for_update: bool = False
    ) -> GenerationState | None: ...

    async def get_by_external(
        self,
        source_application_id: str,
        object_type: str,
        external_generation_id: str,
        *,
        for_update: bool = False,
    ) -> GenerationState | None: ...

    async def get_active(
        self,
        source_application_id: str,
        object_type: str,
        *,
        for_update: bool = False,
    ) -> GenerationState | None: ...

    async def insert(
        self,
        generation: GenerationState,
        *,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None: ...

    async def save(
        self,
        generation: GenerationState,
        *,
        transition_reason: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None: ...

    async def append_staging(
        self, generation_id: UUID, records: Sequence[StagingRecord]
    ) -> None: ...

    async def list_staging(self, generation_id: UUID) -> list[StagingRecord]: ...

    async def get_source_batch(
        self,
        source_application_id: str,
        object_type: str,
        external_batch_id: str,
        *,
        purpose: GenerationPurpose = "production",
    ) -> SourceBatchReceipt | None: ...

    async def put_source_batch(
        self,
        source_application_id: str,
        object_type: str,
        generation_id: UUID,
        batch: AcceptedBatch,
        *,
        purpose: GenerationPurpose = "production",
    ) -> None: ...

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
    ) -> str | None: ...

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
    ) -> dict[str, Any]: ...

    async def list_lease_candidates(
        self, now: datetime
    ) -> list[GenerationState]: ...

    async def list_completing_candidates(
        self, now: datetime
    ) -> list[GenerationState]: ...

    async def get_committed_watermark(
        self, source_application_id: str, object_type: str
    ) -> int: ...

    async def put_committed_watermark(
        self,
        source_application_id: str,
        object_type: str,
        high_watermark: int,
        generation_id: UUID,
    ) -> None: ...

    async def purge_terminal_staging(self, cutoff: datetime) -> int: ...


class InMemoryGenerationStore:
    """In-process generation/staging store used by the protocol simulator."""

    def __init__(self) -> None:
        self.generations: dict[UUID, GenerationState] = {}
        self.staging: dict[UUID, list[StagingRecord]] = {}
        self.current: dict[tuple[str, str, str], IngestRecord] = {}
        self.changes: dict[tuple[str, str, str, int, GenerationPurpose], IngestRecord] = {}
        self.batch_receipts: dict[
            tuple[str, str, str, GenerationPurpose], SourceBatchReceipt
        ] = {}
        self.committed_watermarks: dict[tuple[str, str], int] = {}
        self.updated_at: dict[UUID, datetime] = {}
        self.published_full_count = 0
        self.transitions: list[GenerationTransition] = []
        self._persisted_status: dict[UUID, str] = {}

    async def lock_source(
        self, source_application_id: str, object_type: str
    ) -> None:
        del source_application_id, object_type

    async def get(
        self, generation_id: UUID, *, for_update: bool = False
    ) -> GenerationState | None:
        del for_update
        return self.generations.get(generation_id)

    async def get_by_external(
        self,
        source_application_id: str,
        object_type: str,
        external_generation_id: str,
        *,
        for_update: bool = False,
    ) -> GenerationState | None:
        del for_update
        for generation in self.generations.values():
            if (
                generation.source_application_id == source_application_id
                and generation.object_type == object_type
                and generation.external_generation_id == external_generation_id
            ):
                return generation
        return None

    async def get_active(
        self,
        source_application_id: str,
        object_type: str,
        *,
        for_update: bool = False,
    ) -> GenerationState | None:
        del for_update
        for generation in self.generations.values():
            if (
                generation.source_application_id == source_application_id
                and generation.object_type == object_type
                and generation.status in ACTIVE_GENERATION_STATUSES
            ):
                return generation
        return None

    async def insert(
        self,
        generation: GenerationState,
        *,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None:
        if await self.get_active(generation.source_application_id, generation.object_type):
            raise PushIngestError(
                "generation_in_progress",
                "an active generation already exists for this source/object",
            )
        existing = await self.get_by_external(
            generation.source_application_id,
            generation.object_type,
            generation.external_generation_id,
        )
        if existing is not None:
            raise PushIngestError(
                "generation_digest_conflict",
                "external_generation_id already exists with a different request digest",
            )
        self.generations[generation.generation_id] = generation
        self.staging[generation.generation_id] = []
        self.updated_at[generation.generation_id] = datetime.now(tz=UTC)
        self._persisted_status[generation.generation_id] = generation.status
        self.transitions.append(
            GenerationTransition(
                generation.generation_id,
                None,
                generation.status,
                "create",
                actor=actor,
                request_id=request_id,
            )
        )

    async def save(
        self,
        generation: GenerationState,
        *,
        transition_reason: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None:
        previous = self._persisted_status.get(generation.generation_id)
        if previous != generation.status or transition_reason:
            reason = transition_reason
            if reason is None:
                if generation.status == "EXPIRED":
                    reason = "client_lease_expired"
                elif generation.status == "FAILED":
                    reason = "completion_failed"
                elif previous == "COMPLETING" and generation.status == "COMPLETED":
                    reason = "publish"
                elif generation.status == "COMPLETING":
                    reason = "complete"
                elif generation.status == "ABORTED":
                    reason = "abort"
                elif previous in {None, "OPEN"} and generation.status == "RECEIVING":
                    reason = "first_batch"
                elif previous == "RECEIVING" and generation.status == "RECEIVING":
                    reason = "batch"
            self.transitions.append(
                GenerationTransition(
                    generation.generation_id,
                    previous,
                    generation.status,
                    reason,
                    actor=actor,
                    request_id=request_id,
                )
            )
            self._persisted_status[generation.generation_id] = generation.status
        generation.lock_version += 1
        self.generations[generation.generation_id] = generation
        self.updated_at[generation.generation_id] = datetime.now(tz=UTC)

    async def append_staging(
        self, generation_id: UUID, records: Sequence[StagingRecord]
    ) -> None:
        staged = self.staging.setdefault(generation_id, [])
        seen = {(item.sequence_no, item.record.object_id) for item in staged}
        for item in records:
            key = (item.sequence_no, item.record.object_id)
            if key in seen:
                raise PushIngestError(
                    "invalid_ingest_record",
                    "full batch repeats object_id in the same sequence",
                    status_code=400,
                )
            seen.add(key)
        staged.extend(records)

    async def list_staging(self, generation_id: UUID) -> list[StagingRecord]:
        return list(self.staging.get(generation_id, []))

    async def get_source_batch(
        self,
        source_application_id: str,
        object_type: str,
        external_batch_id: str,
        *,
        purpose: GenerationPurpose = "production",
    ) -> SourceBatchReceipt | None:
        return self.batch_receipts.get(
            (source_application_id, object_type, external_batch_id, purpose)
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
        key = (source_application_id, object_type, batch.external_batch_id, purpose)
        existing = self.batch_receipts.get(key)
        if existing is not None:
            if existing.batch.content_sha256 != batch.content_sha256:
                raise PushIngestError(
                    "batch_digest_conflict",
                    "external_batch_id was reused with a different content digest",
                )
            if existing.batch.raw_batch_id is not None:
                return
            existing.batch.sequence_no = batch.sequence_no
            existing.batch.record_count = batch.record_count
            existing.batch.high_watermark = batch.high_watermark
            existing.batch.payload_contract_version = batch.payload_contract_version
            existing.batch.schema_fingerprint = batch.schema_fingerprint
            existing.batch.raw_batch_id = batch.raw_batch_id
            existing.batch.content_bytes = batch.content_bytes
            self.batch_receipts[key] = SourceBatchReceipt(generation_id, existing.batch)
            return
        self.batch_receipts[key] = SourceBatchReceipt(generation_id, batch)

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
        del high_watermark, payload_contract_version
        del generation_id, content_sha256, schema_fingerprint
        self._apply_change_records_atomically(
            source_application_id=source_application_id,
            object_type=object_type,
            records=records,
            purpose=purpose,
            apply_current_state=apply_current_state,
        )
        return f"mem:{external_batch_id or uuid4()}"

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
        del schema_fingerprint
        from ai_hub_platform.modules.ingest.service import tombstone_version

        validate_ingest_records(records, high_watermark=high_watermark)
        exported = {record.object_id for record in records}
        tombstones = 0

        def _write() -> None:
            nonlocal tombstones
            self._commit_change_records(
                source_application_id=source_application_id,
                object_type=object_type,
                records=records,
                purpose=purpose,
                apply_current_state=apply_current_state,
            )
            if not apply_current_state:
                return
            for (app, kind, object_id), existing in list(self.current.items()):
                if app != source_application_id or kind != object_type:
                    continue
                if object_id in exported:
                    continue
                version = tombstone_version(existing.version, high_watermark)
                tombstone = IngestRecord(object_id, "delete", version, None)
                self._commit_change_records(
                    source_application_id=source_application_id,
                    object_type=object_type,
                    records=[tombstone],
                    purpose=purpose,
                    apply_current_state=True,
                )
                tombstones += 1

        self._with_atomic_raw_writes(_write)
        self.published_full_count += 1
        return {
            "record_count": len(records),
            "tombstones": tombstones,
            "high_watermark": high_watermark,
            "payload_contract_version": payload_contract_version,
            "raw_batch_id": f"mem:full:{generation_id or uuid4()}",
        }

    def _with_atomic_raw_writes(self, mutate: Callable[[], None]) -> None:
        previous_changes = dict(self.changes)
        previous_current = dict(self.current)
        try:
            mutate()
        except Exception:
            self.changes.clear()
            self.changes.update(previous_changes)
            self.current.clear()
            self.current.update(previous_current)
            raise

    def _commit_change_records(
        self,
        *,
        source_application_id: str,
        object_type: str,
        records: Sequence[IngestRecord],
        purpose: GenerationPurpose,
        apply_current_state: bool,
    ) -> None:
        for record in records:
            version_key = (
                source_application_id,
                object_type,
                record.object_id,
                record.version,
            )
            existing_key = next(
                (key for key in self.changes if key[:4] == version_key),
                None,
            )
            if existing_key is not None:
                existing_change = self.changes[existing_key]
                if existing_key[4] != purpose:
                    raise IngestRecordConflictError(
                        f"object_id/version already exists with a different purpose: "
                        f"{record.object_id}@{record.version}"
                    )
                if (
                    existing_change.operation != record.operation
                    or payload_content_hash(existing_change.payload)
                    != payload_content_hash(record.payload)
                ):
                    raise IngestRecordConflictError(
                        f"object_id/version already exists with different content: "
                        f"{record.object_id}@{record.version}"
                    )
            else:
                self.changes[(*version_key, purpose)] = record
            if not apply_current_state:
                continue
            state_key = (source_application_id, object_type, record.object_id)
            existing = self.current.get(state_key)
            if not should_apply_version(
                record.version, existing.version if existing is not None else None
            ):
                continue
            if record.operation == "delete":
                self.current.pop(state_key, None)
            else:
                self.current[state_key] = record

    def _apply_change_records_atomically(
        self,
        *,
        source_application_id: str,
        object_type: str,
        records: Sequence[IngestRecord],
        purpose: GenerationPurpose,
        apply_current_state: bool,
    ) -> None:
        self._with_atomic_raw_writes(
            lambda: self._commit_change_records(
                source_application_id=source_application_id,
                object_type=object_type,
                records=records,
                purpose=purpose,
                apply_current_state=apply_current_state,
            )
        )

    async def list_lease_candidates(self, now: datetime) -> list[GenerationState]:
        return [
            generation
            for generation in self.generations.values()
            if generation.status in {"OPEN", "RECEIVING"}
            and (
                now > generation.client_lease_expires_at
                or _lifetime_exceeded(generation, now)
            )
        ]

    async def list_completing_candidates(self, now: datetime) -> list[GenerationState]:
        return [
            generation
            for generation in self.generations.values()
            if generation.status == "COMPLETING"
            and generation.worker_lease_expires_at is not None
            and now > generation.worker_lease_expires_at
        ]

    async def get_committed_watermark(
        self, source_application_id: str, object_type: str
    ) -> int:
        return self.committed_watermarks.get((source_application_id, object_type), 0)

    async def put_committed_watermark(
        self,
        source_application_id: str,
        object_type: str,
        high_watermark: int,
        generation_id: UUID,
    ) -> None:
        del generation_id
        key = (source_application_id, object_type)
        current = self.committed_watermarks.get(key, 0)
        if high_watermark >= current:
            self.committed_watermarks[key] = high_watermark

    async def purge_terminal_staging(self, cutoff: datetime) -> int:
        removed = 0
        for generation_id, generation in self.generations.items():
            if generation.status not in TERMINAL_GENERATION_STATUSES:
                continue
            updated = self.updated_at.get(generation_id)
            if updated is not None and updated >= cutoff:
                continue
            staged = self.staging.pop(generation_id, [])
            removed += len(staged)
        return removed


class PushGenerationService:
    def __init__(
        self,
        store: GenerationStore,
        *,
        validator: IngestContractValidator | None = None,
        clock: Clock | None = None,
        payload_max_bytes: int = 1_048_576,
        batch_row_limit: int = 5_000,
        pull_enforcement_gate: bool = False,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.store = store
        self.validator = validator or IngestContractValidator()
        self.clock = clock or (lambda: datetime.now(tz=UTC))
        self.payload_max_bytes = payload_max_bytes
        self.batch_row_limit = batch_row_limit
        self.pull_enforcement_gate = pull_enforcement_gate
        self.actor = actor
        self.request_id = request_id

    async def _persist(
        self,
        generation: GenerationState,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("actor", self.actor)
        kwargs.setdefault("request_id", self.request_id)
        await self.store.save(generation, **kwargs)

    async def create_generation(
        self,
        *,
        source: IngestSourceConfig,
        contract: RegisteredContract | None,
        caller_application_id: str,
        external_generation_id: str,
        sync_mode: Literal["full", "incremental"],
        request: Mapping[str, Any],
        lease_seconds: int = 60,
        protocol_version: str = PUSH_PROTOCOL_VERSION,
        purpose: GenerationPurpose = "production",
    ) -> GenerationState:
        _assert_caller(source, caller_application_id)
        digest = canonical_json_digest(dict(request))
        await self.store.lock_source(
            source.source_application_id, source.object_type
        )
        active = await self.store.get_active(
            source.source_application_id,
            source.object_type,
            for_update=True,
        )
        if active is not None:
            await self._expire_if_needed(active)
        existing = await self.store.get_by_external(
            source.source_application_id,
            source.object_type,
            external_generation_id,
            for_update=True,
        )
        if existing is not None:
            if existing.request_digest != digest:
                raise PushIngestError(
                    "generation_digest_conflict",
                    "external_generation_id was reused with a different request digest",
                    details={"generation_id": str(existing.generation_id)},
                )
            return existing
        _assert_writable_push_source(source, purpose=purpose)
        _assert_protocol_version(source, protocol_version)
        if contract is None:
            raise PushIngestError(
                "ingest_contract_rejected",
                "PUSH_AGENT requires an ACTIVE ingest contract",
                status_code=400,
            )
        still_active = await self.store.get_active(
            source.source_application_id, source.object_type
        )
        if still_active is not None:
            raise PushIngestError(
                "generation_in_progress",
                "an active generation already exists for this source/object",
            )
        lease = timedelta(seconds=lease_seconds or int(CLIENT_LEASE.total_seconds()))
        generation = GenerationState(
            generation_id=uuid4(),
            source_application_id=source.source_application_id,
            object_type=source.object_type,
            external_generation_id=external_generation_id,
            request_digest=digest,
            sync_mode=sync_mode,
            status="OPEN",
            next_sequence_no=1,
            client_lease_expires_at=self.clock() + lease,
            payload_contract_version=contract.contract_version,
            schema_fingerprint=contract.schema_fingerprint,
            purpose=purpose,
            created_at=self.clock(),
        )
        await self.store.insert(
            generation, actor=self.actor, request_id=self.request_id
        )
        return generation

    async def peek_generation(self, generation_id: UUID) -> GenerationState:
        generation = await self.store.get(generation_id, for_update=False)
        if generation is None:
            raise PushIngestError(
                "generation_not_found",
                "generation does not exist",
                status_code=404,
            )
        return generation

    async def get_generation(
        self, generation_id: UUID, *, for_update: bool = True
    ) -> GenerationState:
        generation = await self.store.get(generation_id, for_update=for_update)
        if generation is None:
            raise PushIngestError(
                "generation_not_found",
                "generation does not exist",
                status_code=404,
            )
        await self._expire_if_needed(generation)
        return generation

    async def heartbeat(
        self,
        generation_id: UUID,
        *,
        source: IngestSourceConfig,
        caller_application_id: str,
        lease_seconds: int = 60,
    ) -> GenerationState:
        _assert_caller(source, caller_application_id)
        await self.store.lock_source(
            source.source_application_id, source.object_type
        )
        generation = await self.get_generation(generation_id)
        _assert_writable_push_source(source, purpose=generation.purpose)
        if generation.status not in {"OPEN", "RECEIVING"}:
            raise PushIngestError(
                "generation_not_active",
                f"heartbeat is not accepted in status {generation.status}",
            )
        now = self.clock()
        deadline = _generation_deadline(generation)
        if deadline is not None and now >= deadline:
            await self._expire_if_needed(generation)
            raise PushIngestError(
                "generation_not_active",
                f"heartbeat is not accepted in status {generation.status}",
            )
        proposed = now + timedelta(seconds=lease_seconds)
        if deadline is not None and proposed > deadline:
            proposed = deadline
        if proposed < generation.client_lease_expires_at:
            raise PushIngestError(
                "lease_not_monotonic",
                "heartbeat cannot shorten the client lease",
            )
        generation.client_lease_expires_at = proposed
        await self._persist(generation)
        return generation

    async def abort(
        self,
        generation_id: UUID,
        *,
        source: IngestSourceConfig,
        caller_application_id: str,
    ) -> GenerationState:
        _assert_caller(source, caller_application_id)
        await self.store.lock_source(
            source.source_application_id, source.object_type
        )
        generation = await self.get_generation(generation_id)
        _assert_writable_push_source(source, purpose=generation.purpose)
        if generation.status not in {"OPEN", "RECEIVING"}:
            raise PushIngestError(
                "generation_not_active",
                f"abort is not accepted in status {generation.status}",
            )
        generation.status = "ABORTED"
        await self._persist(generation, transition_reason="abort")
        return generation

    async def submit_batch(
        self,
        generation_id: UUID,
        *,
        source: IngestSourceConfig,
        contract: RegisteredContract | None,
        caller_application_id: str,
        sequence_no: int,
        external_batch_id: str,
        records: Sequence[IngestRecord],
        high_watermark: int,
        payload_contract_version: str,
        content_sha256: str,
        schema_fingerprint: str,
    ) -> dict[str, Any]:
        _assert_caller(source, caller_application_id)
        await self.store.lock_source(
            source.source_application_id, source.object_type
        )
        generation = await self.get_generation(generation_id)
        _assert_batch_content_digest(records, content_sha256)
        if generation.status == "EXPIRED":
            replay = _replay_batch(
                generation,
                sequence_no,
                external_batch_id,
                content_sha256,
                high_watermark=high_watermark,
                payload_contract_version=payload_contract_version,
                schema_fingerprint=schema_fingerprint,
                record_count=len(records),
            )
            if replay is not None:
                return replay
            raise PushIngestError(
                "generation_expired",
                "client lease has expired; start a new generation",
            )
        if generation.status not in {"OPEN", "RECEIVING"}:
            if generation.status in {"COMPLETED", "ABORTED", "FAILED"}:
                replay = _replay_batch(
                    generation,
                    sequence_no,
                    external_batch_id,
                    content_sha256,
                    high_watermark=high_watermark,
                    payload_contract_version=payload_contract_version,
                    schema_fingerprint=schema_fingerprint,
                    record_count=len(records),
                )
                if replay is not None:
                    return replay
            raise PushIngestError(
                "generation_not_active",
                f"batches are not accepted in status {generation.status}",
            )
        if self.clock() > generation.client_lease_expires_at:
            generation.status = "EXPIRED"
            await self._persist(generation)
            raise PushIngestError(
                "generation_expired",
                "client lease has expired; start a new generation",
            )

        source_receipt = await self.store.get_source_batch(
            source.source_application_id,
            source.object_type,
            external_batch_id,
            purpose=generation.purpose,
        )
        if source_receipt is not None:
            if source_receipt.batch.content_sha256 != content_sha256:
                raise PushIngestError(
                    "batch_digest_conflict",
                    "external_batch_id was reused with a different content digest",
                )
            _assert_materialized_envelope(
                source_receipt.batch,
                high_watermark=high_watermark,
                payload_contract_version=payload_contract_version,
                schema_fingerprint=schema_fingerprint,
                record_count=len(records),
            )
            if (
                source_receipt.generation_id == generation.generation_id
                or await self._receipt_already_published(source_receipt)
            ):
                return _idempotent_replay(
                    source_receipt.batch,
                    high_watermark=high_watermark,
                    payload_contract_version=payload_contract_version,
                    schema_fingerprint=schema_fingerprint,
                    record_count=len(records),
                )

        existing = next(
            (
                batch
                for batch in generation.accepted_batches
                if batch.external_batch_id == external_batch_id
                or batch.sequence_no == sequence_no
            ),
            None,
        )
        if existing is not None:
            if (
                existing.external_batch_id == external_batch_id
                and                 existing.content_sha256 == content_sha256
                and existing.sequence_no == sequence_no
            ):
                return _idempotent_replay(
                    existing,
                    high_watermark=high_watermark,
                    payload_contract_version=payload_contract_version,
                    schema_fingerprint=schema_fingerprint,
                    record_count=len(records),
                )
            if existing.external_batch_id == external_batch_id:
                raise PushIngestError(
                    "batch_digest_conflict",
                    "external_batch_id was reused with a different content digest",
                )
            if existing.sequence_no == sequence_no:
                raise PushIngestError(
                    "batch_digest_conflict",
                    "sequence_no was reused with a different batch",
                )

        _assert_writable_push_source(source, purpose=generation.purpose)
        if sequence_no != generation.next_sequence_no:
            raise PushIngestError(
                "sequence_gap",
                "sequence_no must be the next expected value",
                details={"expected_sequence_no": generation.next_sequence_no},
            )
        if generation.sync_mode == "full":
            object_ids = [record.object_id for record in records]
            if len(object_ids) != len(set(object_ids)):
                raise PushIngestError(
                    "invalid_ingest_record",
                    "full batch repeats object_id in the same sequence",
                    status_code=400,
                )
        if len(records) > self.batch_row_limit:
            raise PushIngestError(
                "batch_too_large",
                "batch exceeds page_limit_max",
                status_code=400,
                details={"page_limit_max": self.batch_row_limit},
            )
        content_bytes = _records_content_bytes(records)
        _assert_generation_limits(
            generation, record_count=len(records), content_bytes=content_bytes
        )

        try:
            validate_ingest_records(records, high_watermark=high_watermark)
        except IngestValidationError as error:
            raise PushIngestError(
                "invalid_ingest_record",
                str(error),
                status_code=400,
            ) from error

        if not fullmatch(_SHA256_HEX, schema_fingerprint):
            raise PushIngestError(
                "ingest_contract_rejected",
                "schema_fingerprint must be a 64-character sha256 hex digest",
                status_code=400,
            )
        if (
            generation.payload_contract_version is not None
            and payload_contract_version != generation.payload_contract_version
        ):
            raise PushIngestError(
                "ingest_contract_rejected",
                "payload_contract_version cannot change mid-generation",
                status_code=400,
            )
        if (
            generation.schema_fingerprint is not None
            and schema_fingerprint != generation.schema_fingerprint
        ):
            raise PushIngestError(
                "ingest_contract_rejected",
                "schema_fingerprint cannot change mid-generation",
                status_code=400,
            )
        if (
            contract is not None
            and schema_fingerprint != contract.schema_fingerprint
        ):
            raise PushIngestError(
                "ingest_contract_rejected",
                "schema_fingerprint does not match the registered ACTIVE contract",
                status_code=400,
            )

        try:
            accepted_statuses: tuple[str, ...] = ("ACTIVE",)
            if (
                generation.payload_contract_version == payload_contract_version
                and generation.schema_fingerprint == schema_fingerprint
            ):
                accepted_statuses = ("ACTIVE", "DEPRECATED")
            self.validator.validate_records(
                records,
                source=source,
                payload_contract_version=payload_contract_version,
                contract=contract,
                payload_max_bytes=self.payload_max_bytes,
                schema_fingerprint_header=schema_fingerprint,
                accepted_statuses=accepted_statuses,
            )
        except ContractEnforcedError as error:
            raise PushIngestError(
                "ingest_contract_rejected",
                str(error),
                status_code=400,
                details={
                    "issues": [
                        {
                            "code": issue.code,
                            "object_id": issue.object_id,
                            "path": issue.path,
                            "detail": issue.message,
                        }
                        for issue in error.issues
                    ]
                },
            ) from error

        raw_batch_id: str | None = None
        if generation.sync_mode == "incremental":
            if (
                source_receipt is not None
                and source_receipt.batch.raw_batch_id is not None
            ):
                _assert_materialized_envelope(
                    source_receipt.batch,
                    high_watermark=high_watermark,
                    payload_contract_version=payload_contract_version,
                    schema_fingerprint=schema_fingerprint,
                    record_count=len(records),
                )
                raw_batch_id = source_receipt.batch.raw_batch_id
                committed_watermark = source_receipt.batch.high_watermark
            else:
                await self._assert_watermark_not_regressed(
                    generation, high_watermark
                )
                try:
                    raw_batch_id = await self.store.load_incremental(
                        source_application_id=source.source_application_id,
                        object_type=source.object_type,
                        records=records,
                        high_watermark=high_watermark,
                        payload_contract_version=payload_contract_version,
                        generation_id=generation.generation_id,
                        external_batch_id=external_batch_id,
                        content_sha256=content_sha256,
                        schema_fingerprint=schema_fingerprint,
                        apply_current_state=_applies_production_current_state(
                            generation.purpose
                        ),
                        purpose=generation.purpose,
                    )
                except IngestRecordConflictError as error:
                    raise PushIngestError(
                        "record_version_conflict",
                        str(error),
                        status_code=409,
                    ) from error
                committed_watermark = high_watermark
            await self._commit_watermark_if_production(
                generation, committed_watermark
            )
        else:
            await self.store.append_staging(
                generation.generation_id,
                [
                    StagingRecord(sequence_no, record, payload_contract_version)
                    for record in records
                ],
            )

        previous_status = generation.status
        accepted = AcceptedBatch(
            sequence_no=sequence_no,
            external_batch_id=external_batch_id,
            content_sha256=content_sha256,
            record_count=len(records),
            high_watermark=high_watermark,
            payload_contract_version=payload_contract_version,
            schema_fingerprint=schema_fingerprint,
            raw_batch_id=raw_batch_id,
            content_bytes=content_bytes,
        )
        generation.accepted_batches.append(accepted)
        generation.next_sequence_no = sequence_no + 1
        generation.status = "RECEIVING"
        await self.store.put_source_batch(
            source.source_application_id,
            source.object_type,
            generation.generation_id,
            accepted,
            purpose=generation.purpose,
        )
        await self._persist(
            generation,
            transition_reason="first_batch" if previous_status == "OPEN" else "batch",
        )
        return {"idempotent": False, **accepted.as_dict()}

    async def complete(
        self,
        generation_id: UUID,
        *,
        source: IngestSourceConfig,
        caller_application_id: str,
        expected_batch_count: int,
        total_rows: int,
        ordered_batch_digest: str,
        high_watermark: int,
        confirm_empty_full: bool = False,
        publish: bool = True,
    ) -> GenerationState:
        _assert_caller(source, caller_application_id)
        await self.store.lock_source(
            source.source_application_id, source.object_type
        )
        generation = await self.get_generation(generation_id)
        digest = canonical_json_digest(
            {
                "expected_batch_count": expected_batch_count,
                "total_rows": total_rows,
                "ordered_batch_digest": ordered_batch_digest,
                "high_watermark": high_watermark,
            }
        )
        if generation.status == "COMPLETED":
            if generation.completion_digest != digest:
                raise PushIngestError(
                    "generation_digest_conflict",
                    "complete was retried with a different completion digest",
                )
            return generation
        if generation.status == "COMPLETING":
            if generation.completion_digest != digest:
                raise PushIngestError(
                    "generation_digest_conflict",
                    "complete recovery requires the original completion digest",
                )
            if not publish:
                return generation
            return await self._publish_or_map(generation, high_watermark)
        _assert_writable_push_source(source, purpose=generation.purpose)
        if generation.status not in {"OPEN", "RECEIVING"}:
            raise PushIngestError(
                "generation_not_active",
                f"complete is not accepted in status {generation.status}",
            )
        if generation.status == "OPEN" and expected_batch_count != 0:
            raise PushIngestError(
                "sequence_gap",
                "OPEN generations may only complete with zero batches",
                details={"expected_sequence_no": generation.next_sequence_no},
            )
        if expected_batch_count != len(generation.accepted_batches):
            raise PushIngestError(
                "generation_complete_mismatch",
                "expected_batch_count does not match persisted batches",
                status_code=400,
            )
        actual_rows = sum(batch.record_count for batch in generation.accepted_batches)
        if total_rows != actual_rows:
            raise PushIngestError(
                "generation_complete_mismatch",
                "total_rows does not match persisted batches",
                status_code=400,
            )
        actual_digest = _ordered_batch_digest(generation.accepted_batches)
        if actual_digest != ordered_batch_digest:
            raise PushIngestError(
                "generation_complete_mismatch",
                "ordered_batch_digest does not match persisted batches",
                status_code=400,
            )
        persisted_hw = max(
            (batch.high_watermark for batch in generation.accepted_batches),
            default=0,
        )
        committed_hw = await self.store.get_committed_watermark(
            generation.source_application_id, generation.object_type
        )
        previous_hw = max(
            (
                batch.high_watermark
                for batch in generation.accepted_batches[:-1]
            ),
            default=0,
        )
        minimum_hw = max(persisted_hw, committed_hw, previous_hw)
        if high_watermark < minimum_hw:
            raise PushIngestError(
                "generation_complete_mismatch",
                "high_watermark is lower than the committed or persisted watermark",
                status_code=409,
                details={"minimum_high_watermark": minimum_hw},
            )
        if (
            generation.sync_mode == "full"
            and actual_rows == 0
            and not (source.allow_empty_full and confirm_empty_full)
        ):
            return await self._fail_generation(generation, "empty_full_not_allowed")
        if generation.sync_mode == "full":
            staged = await self.store.list_staging(generation.generation_id)
            try:
                validate_ingest_records(
                    [item.record for item in staged],
                    high_watermark=high_watermark,
                )
            except IngestValidationError:
                return await self._fail_generation(
                    generation, "generation_complete_mismatch"
                )
            object_ids = [item.record.object_id for item in staged]
            if len(object_ids) != len(set(object_ids)):
                return await self._fail_generation(
                    generation, "duplicate_object_in_full"
                )

        generation.status = "COMPLETING"
        generation.completion_digest = digest
        generation.completion_request = {
            "expected_batch_count": expected_batch_count,
            "total_rows": total_rows,
            "ordered_batch_digest": ordered_batch_digest,
            "high_watermark": high_watermark,
            "confirm_empty_full": confirm_empty_full,
        }
        generation.worker_lease_expires_at = self.clock() + WORKER_LEASE
        await self._persist(generation, transition_reason="complete")
        if not publish:
            return generation
        return await self._publish_or_map(generation, high_watermark)

    async def _assert_watermark_not_regressed(
        self, generation: GenerationState, high_watermark: int
    ) -> None:
        committed_hw = await self.store.get_committed_watermark(
            generation.source_application_id, generation.object_type
        )
        previous_hw = max(
            (batch.high_watermark for batch in generation.accepted_batches),
            default=0,
        )
        minimum_hw = max(committed_hw, previous_hw)
        if high_watermark < minimum_hw:
            raise PushIngestError(
                "generation_complete_mismatch",
                "high_watermark is lower than the committed or persisted watermark",
                status_code=409,
                details={"minimum_high_watermark": minimum_hw},
            )

    async def _commit_watermark_if_production(
        self, generation: GenerationState, high_watermark: int
    ) -> None:
        if not _applies_production_current_state(generation.purpose):
            return
        await self.store.put_committed_watermark(
            generation.source_application_id,
            generation.object_type,
            high_watermark,
            generation.generation_id,
        )

    async def expire_one(self, generation_id: UUID) -> UUID | None:
        peeked = await self.store.get(generation_id, for_update=False)
        if peeked is None:
            return None
        await self.store.lock_source(
            peeked.source_application_id, peeked.object_type
        )
        now = self.clock()
        generation = await self.store.get(generation_id, for_update=True)
        if generation is None:
            return None
        if generation.status in {"OPEN", "RECEIVING"} and (
            now > generation.client_lease_expires_at
            or _lifetime_exceeded(generation, now)
        ):
            generation.status = "EXPIRED"
            await self._persist(
                generation,
                transition_reason=(
                    "generation_lifetime_exceeded"
                    if _lifetime_exceeded(generation, now)
                    else "client_lease_expired"
                ),
                actor="system:lease_reaper",
                request_id=str(generation.generation_id),
            )
            return generation.generation_id
        return None

    async def recover_one(self, generation_id: UUID) -> UUID | None:
        peeked = await self.store.get(generation_id, for_update=False)
        if peeked is None:
            return None
        await self.store.lock_source(
            peeked.source_application_id, peeked.object_type
        )
        now = self.clock()
        generation = await self.store.get(generation_id, for_update=True)
        if (
            generation is None
            or generation.status != "COMPLETING"
            or generation.worker_lease_expires_at is None
            or now <= generation.worker_lease_expires_at
        ):
            return None
        request = generation.completion_request or {}
        high_watermark = int(request.get("high_watermark", 0))
        generation.worker_lease_expires_at = now + WORKER_LEASE
        await self._persist(
            generation,
            transition_reason="worker_recover",
            actor="system:worker_recover",
            request_id=str(generation.generation_id),
        )
        published = await self._publish_or_map(generation, high_watermark)
        return published.generation_id

    async def expire_stale(self) -> list[UUID]:
        expired: list[UUID] = []
        for candidate in await self.store.list_lease_candidates(self.clock()):
            try:
                result = await self.expire_one(candidate.generation_id)
            except Exception:
                LOGGER.exception(
                    "push generation expire failed generation_id=%s",
                    candidate.generation_id,
                )
                continue
            if result is not None:
                expired.append(result)
        return expired

    async def recover_completing(self) -> list[UUID]:
        recovered: list[UUID] = []
        for candidate in await self.store.list_completing_candidates(self.clock()):
            try:
                result = await self.recover_one(candidate.generation_id)
            except Exception:
                LOGGER.exception(
                    "push generation recover failed generation_id=%s",
                    candidate.generation_id,
                )
                continue
            if result is not None:
                recovered.append(result)
        return recovered

    async def purge_stale_staging(
        self, *, retention: timedelta | None = None
    ) -> int:
        hold = STAGING_RETENTION if retention is None else retention
        return await self.store.purge_terminal_staging(self.clock() - hold)

    async def _expire_if_needed(self, generation: GenerationState) -> None:
        if generation.status not in {"OPEN", "RECEIVING"}:
            return
        now = self.clock()
        lifetime = _lifetime_exceeded(generation, now)
        if not lifetime and now <= generation.client_lease_expires_at:
            return
        generation.status = "EXPIRED"
        await self._persist(
            generation,
            transition_reason=(
                "generation_lifetime_exceeded" if lifetime else "client_lease_expired"
            ),
        )

    async def _fail_generation(
        self, generation: GenerationState, error_code: str
    ) -> GenerationState:
        generation.status = "FAILED"
        generation.error_code = error_code
        await self._persist(generation)
        return generation

    async def _receipt_already_published(self, receipt: SourceBatchReceipt) -> bool:
        if receipt.batch.raw_batch_id is not None:
            return True
        owner = await self.store.get(receipt.generation_id, for_update=False)
        return owner is not None and owner.status == "COMPLETED"

    async def _publish_or_map(
        self, generation: GenerationState, high_watermark: int
    ) -> GenerationState:
        try:
            return await self._publish(generation, high_watermark)
        except IngestValidationError:
            return await self._fail_generation(
                generation, "generation_complete_mismatch"
            )

    async def _publish(
        self,
        generation: GenerationState,
        high_watermark: int,
    ) -> GenerationState:
        if generation.sync_mode == "full":
            staged = await self.store.list_staging(generation.generation_id)
            records = [item.record for item in staged]
            contract_version = generation.payload_contract_version or (
                staged[0].payload_contract_version if staged else "unknown"
            )
            receipt = await self.store.publish_full(
                source_application_id=generation.source_application_id,
                object_type=generation.object_type,
                records=records,
                high_watermark=high_watermark,
                payload_contract_version=contract_version,
                generation_id=generation.generation_id,
                schema_fingerprint=generation.schema_fingerprint,
                apply_current_state=_applies_production_current_state(
                    generation.purpose
                ),
                purpose=generation.purpose,
            )
            raw_batch_id = receipt.get("raw_batch_id")
            if isinstance(raw_batch_id, str) and raw_batch_id:
                for batch in generation.accepted_batches:
                    if batch.raw_batch_id is None:
                        batch.raw_batch_id = raw_batch_id
                    await self.store.put_source_batch(
                        generation.source_application_id,
                        generation.object_type,
                        generation.generation_id,
                        batch,
                        purpose=generation.purpose,
                    )
        else:
            receipt = {
                "record_count": sum(
                    batch.record_count for batch in generation.accepted_batches
                ),
                "tombstones": 0,
                "high_watermark": high_watermark,
            }
        await self._commit_watermark_if_production(generation, high_watermark)
        generation.status = "COMPLETED"
        generation.final_receipt = receipt
        await self._persist(generation)
        return generation


def _applies_production_current_state(purpose: GenerationPurpose) -> bool:
    return purpose != "certification"


def _generation_deadline(generation: GenerationState) -> datetime | None:
    if generation.created_at is None:
        return None
    return generation.created_at + PUSH_MAX_GENERATION_LIFETIME


def _lifetime_exceeded(generation: GenerationState, now: datetime) -> bool:
    deadline = _generation_deadline(generation)
    if deadline is None:
        return False
    return now >= deadline


def _records_content_bytes(records: Sequence[IngestRecord]) -> int:
    total = 0
    for record in records:
        total += len(record.object_id.encode())
        if record.payload is None:
            continue
        total += len(
            json.dumps(
                dict(record.payload),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
    return total


def _assert_generation_limits(
    generation: GenerationState, *, record_count: int, content_bytes: int
) -> None:
    if len(generation.accepted_batches) >= PUSH_MAX_BATCHES:
        raise PushIngestError(
            "generation_limit_exceeded",
            "generation exceeds the maximum number of batches",
            status_code=409,
            details={"max_batches": PUSH_MAX_BATCHES},
        )
    total_rows = (
        sum(batch.record_count for batch in generation.accepted_batches) + record_count
    )
    if total_rows > PUSH_MAX_GENERATION_ROWS:
        raise PushIngestError(
            "generation_limit_exceeded",
            "generation exceeds the maximum number of rows",
            status_code=409,
            details={"max_generation_rows": PUSH_MAX_GENERATION_ROWS},
        )
    total_bytes = (
        sum(batch.content_bytes for batch in generation.accepted_batches) + content_bytes
    )
    if total_bytes > PUSH_MAX_GENERATION_BYTES:
        raise PushIngestError(
            "generation_limit_exceeded",
            "generation exceeds the maximum content size",
            status_code=409,
            details={"max_generation_bytes": PUSH_MAX_GENERATION_BYTES},
        )


def _assert_caller(source: IngestSourceConfig, caller_application_id: str) -> None:
    if caller_application_id != source.source_application_id:
        raise PushIngestError(
            "source_impersonation_denied",
            "token application_id does not match the registered source",
            status_code=403,
        )


def _assert_writable_push_source(
    source: IngestSourceConfig,
    *,
    purpose: GenerationPurpose = "production",
) -> None:
    if source.transport_mode != "PUSH_AGENT":
        raise PushIngestError(
            "source_not_push_agent",
            "Push API only accepts sources registered as PUSH_AGENT",
            status_code=400,
        )
    if purpose == "certification":
        # Certification may observe a disabled source, but those batches must
        # not apply to production current_state or committed watermarks.
        return
    if not source.enabled:
        raise PushIngestError(
            "source_disabled",
            "Push source is disabled",
            status_code=409,
        )


def _assert_materialized_envelope(
    receipt: AcceptedBatch,
    *,
    high_watermark: int,
    payload_contract_version: str,
    schema_fingerprint: str,
    record_count: int,
) -> None:
    if (
        receipt.high_watermark != high_watermark
        or receipt.payload_contract_version != payload_contract_version
        or receipt.schema_fingerprint != schema_fingerprint
        or receipt.record_count != record_count
    ):
        raise PushIngestError(
            "batch_digest_conflict",
            "external_batch_id was reused with a different batch envelope",
            status_code=409,
            details={
                "high_watermark": receipt.high_watermark,
                "payload_contract_version": receipt.payload_contract_version,
                "schema_fingerprint": receipt.schema_fingerprint,
                "record_count": receipt.record_count,
            },
        )


def _assert_batch_content_digest(
    records: Sequence[IngestRecord], content_sha256: str
) -> None:
    if _batch_content_digest(records) != content_sha256:
        raise PushIngestError(
            "batch_digest_conflict",
            "content_sha256 does not match the canonical record digest",
            status_code=400,
        )


def _assert_protocol_version(source: IngestSourceConfig, protocol_version: str) -> None:
    if (
        protocol_version != PUSH_PROTOCOL_VERSION
        or source.push_protocol_version != protocol_version
    ):
        raise PushIngestError(
            "unsupported_protocol_version",
            "protocol_version is not supported for this source",
            status_code=400,
            details={
                "protocol_version": protocol_version,
                "supported": [PUSH_PROTOCOL_VERSION],
            },
        )


def _batch_content_digest(records: Sequence[IngestRecord]) -> str:
    payload = [
        {
            "object_id": record.object_id,
            "operation": record.operation,
            "version": record.version,
            "payload": None if record.payload is None else dict(record.payload),
            "content_hash": payload_content_hash(record.payload),
        }
        for record in records
    ]
    return canonical_json_digest(payload)


def _ordered_batch_digest(batches: Sequence[AcceptedBatch]) -> str:
    return canonical_json_digest(
        [
            {
                "sequence_no": batch.sequence_no,
                "external_batch_id": batch.external_batch_id,
                "content_sha256": batch.content_sha256,
            }
            for batch in batches
        ]
    )


def _idempotent_replay(
    batch: AcceptedBatch,
    *,
    high_watermark: int,
    payload_contract_version: str,
    schema_fingerprint: str,
    record_count: int,
) -> dict[str, Any]:
    _assert_materialized_envelope(
        batch,
        high_watermark=high_watermark,
        payload_contract_version=payload_contract_version,
        schema_fingerprint=schema_fingerprint,
        record_count=record_count,
    )
    return {"idempotent": True, **batch.as_dict()}


def _replay_batch(
    generation: GenerationState,
    sequence_no: int,
    external_batch_id: str,
    content_sha256: str,
    *,
    high_watermark: int,
    payload_contract_version: str,
    schema_fingerprint: str,
    record_count: int,
) -> dict[str, Any] | None:
    for batch in generation.accepted_batches:
        if (
            batch.sequence_no == sequence_no
            and batch.external_batch_id == external_batch_id
            and batch.content_sha256 == content_sha256
        ):
            return _idempotent_replay(
                batch,
                high_watermark=high_watermark,
                payload_contract_version=payload_contract_version,
                schema_fingerprint=schema_fingerprint,
                record_count=record_count,
            )
    return None


def batch_content_sha256(records: Sequence[IngestRecord]) -> str:
    return _batch_content_digest(records)


def ordered_batch_digest(batches: Sequence[AcceptedBatch]) -> str:
    return _ordered_batch_digest(batches)
