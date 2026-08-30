"""Pull-mode ingest scheduler: lookback fetch, load, cursor advance, failure rollback."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import signal
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ai_hub_sdk import OidcClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ai_hub_platform.config import RawWorkerSettings
from ai_hub_platform.modules.ingest.config_store import IngestConfigStore
from ai_hub_platform.modules.ingest.contract import (
    IngestContractValidator,
    RegisteredContract,
    audit_summary_payload,
    load_active_contract,
)
from ai_hub_platform.modules.ingest.export_client import (
    EXPORT_TOKEN_SCOPES,
    ExportClient,
    ExportClientError,
    ExportPage,
    records_to_ingest,
)
from ai_hub_platform.modules.ingest.service import IngestRecord, IngestService, SyncMode
from ai_hub_platform.modules.ingest.source_lock import lock_ingest_source
from ai_hub_platform.modules.ingest.sources import (
    IngestSourceConfig,
    compute_since_version,
    load_source_configs_from_db,
    pull_export_sources,
)

LOGGER = logging.getLogger(__name__)
SleepFn = Callable[[float], Awaitable[Any]]


@dataclass(slots=True)
class SchedulerMetrics:
    sync_started: int = 0
    sync_succeeded: int = 0
    sync_failed: int = 0
    pages_fetched: int = 0
    records_loaded: int = 0
    full_syncs: int = 0
    incremental_syncs: int = 0
    contract_audit_issues: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "sync_started": self.sync_started,
            "sync_succeeded": self.sync_succeeded,
            "sync_failed": self.sync_failed,
            "pages_fetched": self.pages_fetched,
            "records_loaded": self.records_loaded,
            "full_syncs": self.full_syncs,
            "incremental_syncs": self.incremental_syncs,
            "contract_audit_issues": self.contract_audit_issues,
        }


@dataclass(slots=True)
class _SourceRuntime:
    config: IngestSourceConfig
    next_run_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class IngestScheduler:
    def __init__(
        self,
        settings: RawWorkerSettings,
        *,
        sources: Sequence[IngestSourceConfig],
        sessions: async_sessionmaker[AsyncSession],
        export_client: ExportClient,
        service: IngestService | None = None,
        sleep: SleepFn | None = None,
        clock: Callable[[], float] | None = None,
        contract_validator: IngestContractValidator | None = None,
        contract_lookup: (
            Callable[[IngestSourceConfig, str], RegisteredContract | None]
            | Callable[[IngestSourceConfig, str], Awaitable[RegisteredContract | None]]
            | None
        ) = None,
        payload_max_bytes: int = 1_048_576,
    ) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{uuid4()}"
        self.sessions = sessions
        self.export_client = export_client
        self.service = service or IngestService()
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._clock = clock or time.monotonic
        self.contract_validator = contract_validator
        self.contract_lookup = contract_lookup
        self.payload_max_bytes = payload_max_bytes
        self.metrics = SchedulerMetrics()
        self._global_sem = asyncio.Semaphore(settings.max_concurrent_sources)
        self._app_sems: dict[str, asyncio.Semaphore] = {}
        self._runtimes: dict[tuple[str, str], _SourceRuntime] = {}
        self._replace_runtimes(sources)

    def _replace_runtimes(self, sources: Sequence[IngestSourceConfig]) -> None:
        enabled = [source for source in sources if source.enabled]
        desired = {source.source_key: source for source in enabled}
        for key in list(self._runtimes):
            if key not in desired:
                del self._runtimes[key]
        for key, config in desired.items():
            existing = self._runtimes.get(key)
            if existing is None:
                self._runtimes[key] = _SourceRuntime(config=config)
            else:
                existing.config = config

    async def reload_sources(
        self, loader: Callable[[], Awaitable[list[IngestSourceConfig]]]
    ) -> None:
        """Hot-reload source configs from the authoritative store (design §2.5.1)."""
        try:
            sources = await loader()
        except Exception:
            LOGGER.exception(
                json.dumps(
                    {"event": "ingest_config_reload_failed", "worker_id": self.worker_id},
                    separators=(",", ":"),
                )
            )
            return
        self._replace_runtimes(sources)
        await self._refresh_policy()

    async def _refresh_policy(self) -> None:
        try:
            async with self.sessions() as session:
                policy = await IngestConfigStore().get_policy(session)
        except Exception:
            LOGGER.exception(
                json.dumps(
                    {
                        "event": "ingest_policy_reload_failed",
                        "worker_id": self.worker_id,
                    },
                    separators=(",", ":"),
                )
            )
            return
        self.payload_max_bytes = policy.payload_max_bytes

    def _app_semaphore(self, application_id: str) -> asyncio.Semaphore:
        semaphore = self._app_sems.get(application_id)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.settings.max_concurrent_per_application)
            self._app_sems[application_id] = semaphore
        return semaphore

    async def sync_source(
        self,
        source: IngestSourceConfig,
        *,
        force_full: bool | None = None,
    ) -> dict[str, Any]:
        """Run one pull cycle for a source. Serial per (app, object_type)."""
        self.metrics.sync_started += 1
        async with self._global_sem:
            async with self._app_semaphore(source.source_application_id):
                try:
                    result = await self._sync_source_unlocked(
                        source, force_full=force_full
                    )
                except Exception as error:
                    self.metrics.sync_failed += 1
                    await self._mark_failure(source, error)
                    LOGGER.exception(
                        json.dumps(
                            {
                                "event": "ingest_sync_failed",
                                "worker_id": self.worker_id,
                                "source_application_id": source.source_application_id,
                                "object_type": source.object_type,
                                "error_type": type(error).__name__,
                                "metrics": self.metrics.as_dict(),
                            },
                            separators=(",", ":"),
                        )
                    )
                    raise
                self.metrics.sync_succeeded += 1
                LOGGER.info(
                    json.dumps(
                        {
                            "event": "ingest_sync_succeeded",
                            "worker_id": self.worker_id,
                            **result,
                            "metrics": self.metrics.as_dict(),
                        },
                        separators=(",", ":"),
                    )
                )
                return result

    async def _sync_source_unlocked(
        self,
        source: IngestSourceConfig,
        *,
        force_full: bool | None,
    ) -> dict[str, Any]:
        async with self.sessions() as session:
            async with session.begin():
                last_version = await self.service.get_cursor(
                    session,
                    source_application_id=source.source_application_id,
                    object_type=source.object_type,
                )

        use_full = force_full if force_full is not None else last_version == 0
        since_version = (
            0
            if use_full
            else compute_since_version(last_version, source.lookback_versions)
        )

        if use_full:
            self.metrics.full_syncs += 1
            return await self._run_full_sync(source, since_version=since_version)

        self.metrics.incremental_syncs += 1
        return await self._run_incremental_sync(
            source,
            since_version=since_version,
            baseline_cursor=last_version,
        )

    def _pull_url(self, source: IngestSourceConfig) -> str:
        if source.export_base_url is None:
            raise ExportClientError(
                f"PULL_EXPORT source {source.source_application_id}/{source.object_type} "
                "is missing export_base_url"
            )
        return source.export_base_url

    async def _lookup_contract(
        self,
        source: IngestSourceConfig,
        payload_contract_version: str,
        session: AsyncSession | None = None,
    ) -> RegisteredContract | None:
        if self.contract_lookup is not None:
            result = self.contract_lookup(source, payload_contract_version)
            if inspect.isawaitable(result):
                return await result
            return result
        if session is not None:
            try:
                return await load_active_contract(
                    session,
                    source_application_id=source.source_application_id,
                    object_type=source.object_type,
                    contract_version=payload_contract_version,
                )
            except AttributeError:
                return None
        async with self.sessions() as owned:
            try:
                return await load_active_contract(
                    owned,
                    source_application_id=source.source_application_id,
                    object_type=source.object_type,
                    contract_version=payload_contract_version,
                )
            except AttributeError:
                return None

    async def _validate_pull_page(
        self, source: IngestSourceConfig, page: ExportPage
    ) -> None:
        if self.contract_validator is None:
            return
        contract = await self._lookup_contract(source, page.payload_contract_version)
        self.contract_validator.validate_records(
            records_to_ingest(page.records),
            source=source,
            payload_contract_version=page.payload_contract_version,
            contract=contract,
            payload_max_bytes=self.payload_max_bytes,
            pull_enforcement_gate=self.settings.ingest_pull_contract_enforcement_enabled,
        )

    async def _audit_pull_page(
        self, source: IngestSourceConfig, page: ExportPage
    ) -> None:
        # Pre-lock: collect audit issues only. Cached ENFORCE must not reject a
        # page after operators have already reverted the source to AUDIT_ONLY.
        await self._validate_pull_page(
            source.model_copy(update={"contract_validation_mode": "AUDIT_ONLY"}),
            page,
        )

    async def _require_locked_pull_source(
        self, session: AsyncSession, source: IngestSourceConfig
    ) -> IngestSourceConfig:
        row = await IngestConfigStore().get_source(
            session,
            source_application_id=source.source_application_id,
            object_type=source.object_type,
        )
        if (
            row is None
            or not row.config.enabled
            or row.config.transport_mode != "PULL_EXPORT"
        ):
            raise ExportClientError(
                f"ingest source {source.source_application_id}/{source.object_type} "
                "is no longer an enabled PULL_EXPORT source"
            )
        return row.config

    async def _validate_locked_records(
        self,
        source: IngestSourceConfig,
        records: list[IngestRecord],
        payload_contract_version: str,
        session: AsyncSession,
    ) -> tuple[str | None, dict[str, Any] | None]:
        contract = await self._lookup_contract(
            source, payload_contract_version, session=session
        )
        fingerprint = contract.schema_fingerprint if contract is not None else None
        audit_summary: dict[str, Any] | None = None
        if self.contract_validator is None:
            return fingerprint, audit_summary
        result = self.contract_validator.validate_records(
            records,
            source=source,
            payload_contract_version=payload_contract_version,
            contract=contract,
            payload_max_bytes=self.payload_max_bytes,
            pull_enforcement_gate=self.settings.ingest_pull_contract_enforcement_enabled,
        )
        if result.status == "audit" and result.issues:
            audit_summary = audit_summary_payload(result.issues)
            self.metrics.contract_audit_issues += len(result.issues)
        return fingerprint, audit_summary

    async def _run_full_sync(
        self,
        source: IngestSourceConfig,
        *,
        since_version: int,
    ) -> dict[str, Any]:
        # Collect every page before load so absence tombstones see the full snapshot.
        records, high_watermark, contract = await self.export_client.fetch_all_pages(
            export_base_url=self._pull_url(source),
            object_type=source.object_type,
            since_version=since_version,
            limit=source.page_limit,
            mode="full",
            on_page=lambda page: self._audit_pull_page(source, page),
        )
        self.metrics.pages_fetched += 1
        ingest_records = records_to_ingest(records)
        async with self.sessions() as session:
            async with session.begin():
                await lock_ingest_source(
                    session,
                    source.source_application_id,
                    source.object_type,
                )
                locked = await self._require_locked_pull_source(session, source)
                fingerprint, audit_summary = await self._validate_locked_records(
                    locked, ingest_records, contract, session
                )
                loaded = await self.service.load_batch(
                    session,
                    source_application_id=source.source_application_id,
                    object_type=source.object_type,
                    sync_mode="full",
                    records=ingest_records,
                    high_watermark=high_watermark,
                    payload_contract_version=contract,
                    from_version=since_version,
                    schema_fingerprint=fingerprint,
                    audit_summary=audit_summary,
                )
                await self.service.advance_cursor(
                    session,
                    source_application_id=source.source_application_id,
                    object_type=source.object_type,
                    last_version=high_watermark,
                    status="ok",
                )
        self.metrics.records_loaded += len(ingest_records)
        return {
            "source_application_id": source.source_application_id,
            "object_type": source.object_type,
            "sync_mode": "full",
            "since_version": since_version,
            "high_watermark": high_watermark,
            "record_count": len(ingest_records),
            "batch_id": str(loaded.batch_id),
            "tombstones": loaded.tombstones,
        }

    async def _run_incremental_sync(
        self,
        source: IngestSourceConfig,
        *,
        since_version: int,
        baseline_cursor: int,
    ) -> dict[str, Any]:
        cursor = since_version
        total_records = 0
        pages = 0
        final_hw = baseline_cursor
        last_batch_id: str | None = None
        while True:
            page = await self.export_client.fetch_page(
                export_base_url=self._pull_url(source),
                object_type=source.object_type,
                since_version=cursor,
                limit=source.page_limit,
            )
            pages += 1
            self.metrics.pages_fetched += 1
            if page.object_type != source.object_type:
                raise ExportClientError(
                    f"export object_type mismatch: expected {source.object_type}, "
                    f"got {page.object_type}"
                )
            ingest_records = records_to_ingest(page.records)
            await self._audit_pull_page(source, page)
            async with self.sessions() as session:
                async with session.begin():
                    await lock_ingest_source(
                        session,
                        source.source_application_id,
                        source.object_type,
                    )
                    locked = await self._require_locked_pull_source(session, source)
                    fingerprint, audit_summary = await self._validate_locked_records(
                        locked, ingest_records, page.payload_contract_version, session
                    )
                    loaded = await self.service.load_batch(
                        session,
                        source_application_id=source.source_application_id,
                        object_type=source.object_type,
                        sync_mode="incremental",
                        records=ingest_records,
                        high_watermark=page.high_watermark,
                        payload_contract_version=page.payload_contract_version,
                        from_version=cursor,
                        schema_fingerprint=fingerprint,
                        audit_summary=audit_summary,
                    )
                    await self.service.advance_cursor(
                        session,
                        source_application_id=source.source_application_id,
                        object_type=source.object_type,
                        last_version=page.high_watermark,
                        status="ok",
                    )
            total_records += len(ingest_records)
            self.metrics.records_loaded += len(ingest_records)
            final_hw = page.high_watermark
            last_batch_id = str(loaded.batch_id)
            if not page.has_more:
                break
            if not page.records:
                raise ExportClientError(
                    "export page claimed has_more without returning records"
                )
            next_since = max(record.version for record in page.records)
            if next_since <= cursor:
                raise ExportClientError(
                    "export pagination did not advance since_version"
                )
            cursor = next_since
        return {
            "source_application_id": source.source_application_id,
            "object_type": source.object_type,
            "sync_mode": "incremental",
            "since_version": since_version,
            "high_watermark": final_hw,
            "record_count": total_records,
            "pages": pages,
            "batch_id": last_batch_id,
        }

    async def _mark_failure(self, source: IngestSourceConfig, error: BaseException) -> None:
        message = f"{type(error).__name__}: {error}"
        try:
            async with self.sessions() as session:
                async with session.begin():
                    cursor = await self.service.get_cursor(
                        session,
                        source_application_id=source.source_application_id,
                        object_type=source.object_type,
                    )
                    sync_mode: SyncMode = "full" if cursor == 0 else "incremental"
                    await self.service.record_failed_batch(
                        session,
                        source_application_id=source.source_application_id,
                        object_type=source.object_type,
                        sync_mode=sync_mode,
                        from_version=cursor,
                        error=message,
                    )
                    await self.service.advance_cursor(
                        session,
                        source_application_id=source.source_application_id,
                        object_type=source.object_type,
                        last_version=cursor,
                        status="failed",
                    )
        except Exception:
            LOGGER.exception(
                json.dumps(
                    {
                        "event": "ingest_failure_mark_failed",
                        "source_application_id": source.source_application_id,
                        "object_type": source.object_type,
                    },
                    separators=(",", ":"),
                )
            )

    async def run(
        self,
        stop: asyncio.Event,
        *,
        reload_loader: Callable[[], Awaitable[list[IngestSourceConfig]]] | None = None,
        reload_interval_ticks: int = 15,
    ) -> None:
        if not self._runtimes:
            LOGGER.warning(
                json.dumps(
                    {
                        "event": "ingest_scheduler_idle",
                        "worker_id": self.worker_id,
                        "reason": "no_enabled_sources",
                    },
                    separators=(",", ":"),
                )
            )
        tick = 0
        while not stop.is_set():
            tick += 1
            if reload_loader is not None and tick % reload_interval_ticks == 0:
                await self.reload_sources(reload_loader)
            now = self._clock()
            due = [
                runtime
                for runtime in self._runtimes.values()
                if runtime.next_run_at <= now and not runtime.lock.locked()
            ]
            tasks = [asyncio.create_task(self._run_due(runtime)) for runtime in due]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.settings.tick_interval_seconds
                )
            except TimeoutError:
                pass

    async def _run_due(self, runtime: _SourceRuntime) -> None:
        if runtime.lock.locked():
            return
        async with runtime.lock:
            try:
                await self.sync_source(runtime.config)
            except Exception:
                # Logged in sync_source; keep scheduling.
                pass
            finally:
                runtime.next_run_at = self._clock() + runtime.config.interval_seconds


async def create_runtime_scheduler(
    settings: RawWorkerSettings,
    *,
    sources: Sequence[IngestSourceConfig],
    sessions: async_sessionmaker[AsyncSession],
    export_client: ExportClient,
    service: IngestService | None = None,
) -> IngestScheduler:
    """Construct a scheduler with DB contract lookup and the global size policy."""
    async with sessions() as session:
        policy = await IngestConfigStore().get_policy(session)
    return IngestScheduler(
        settings,
        sources=sources,
        sessions=sessions,
        export_client=export_client,
        service=service,
        contract_validator=IngestContractValidator(),
        payload_max_bytes=policy.payload_max_bytes,
    )


async def run_ingest_scheduler(settings: RawWorkerSettings) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, stop.set)

    engine: AsyncEngine = create_async_engine(settings.raw_database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def load_sources() -> list[IngestSourceConfig]:
        async with sessions() as session:
            return pull_export_sources(await load_source_configs_from_db(session))

    async with sessions() as session:
        initial_sources = pull_export_sources(await load_source_configs_from_db(session))

    oidc = OidcClient(
        settings.oidc_issuer,
        settings.oidc_client_id,
        settings.oidc_client_secret.get_secret_value(),
    )

    async def token_provider() -> str:
        return await oidc.client_credentials_token(EXPORT_TOKEN_SCOPES)

    export_client = ExportClient(
        token_provider=token_provider,
        timeout_seconds=settings.http_timeout_seconds,
    )
    scheduler = await create_runtime_scheduler(
        settings,
        sources=initial_sources,
        sessions=sessions,
        export_client=export_client,
    )
    try:
        await scheduler.run(stop, reload_loader=load_sources)
    finally:
        await export_client.close()
        await oidc.close()
        await engine.dispose()
