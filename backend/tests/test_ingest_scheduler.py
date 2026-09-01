"""Unit tests for M7-02 ingest scheduler, sources, and export client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from ai_hub_platform.config import RawWorkerSettings
from ai_hub_platform.modules.ingest.export_client import (
    EXPORT_SCOPE,
    EXPORT_TOKEN_SCOPES,
    ExportClient,
    ExportClientError,
)
from ai_hub_platform.modules.ingest.reconcile import (
    IngestReconcileService,
    ReconcileReport,
)
from ai_hub_platform.modules.ingest.scheduler import IngestScheduler
from ai_hub_platform.modules.ingest.service import IngestRecord, IngestValidationError
from ai_hub_platform.modules.ingest.sources import (
    IngestSourceConfig,
    IngestSourcesDocument,
    IngestSourcesError,
    compute_since_version,
    load_ingest_sources,
)
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_compute_since_version_applies_lookback_floor() -> None:
    assert compute_since_version(0, 100) == 0
    assert compute_since_version(50, 100) == 0
    assert compute_since_version(150, 100) == 50
    assert compute_since_version(100, 0) == 100


def test_ingest_sources_document_rejects_duplicates(tmp_path: Any) -> None:
    path = tmp_path / "sources.json"
    path.write_text(
        """
        {
          "schema_version": 1,
          "sources": [
            {
              "source_application_id": "app-a",
              "object_type": "device",
              "export_base_url": "http://app-a:8000",
              "enabled": true
            },
            {
              "source_application_id": "app-a",
              "object_type": "device",
              "export_base_url": "http://app-a:8000",
              "enabled": false
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    with pytest.raises(IngestSourcesError, match="duplicate"):
        load_ingest_sources(path)


def test_ingest_sources_loads_repo_default() -> None:
    document = load_ingest_sources(PROJECT_ROOT / "deploy/operations/ingest-sources.json")
    assert document.schema_version == 1
    assert len(document.sources) >= 1
    assert document.sources[0].enabled is False


def test_raw_worker_settings_validate_concurrency_budget() -> None:
    with pytest.raises(ValidationError, match="max_concurrent_per_application"):
        RawWorkerSettings(
            max_concurrent_sources=2,
            max_concurrent_per_application=4,
        )
    settings = RawWorkerSettings(
        oidc_client_secret=SecretStr("local-only-oidc-client-secret"),
    )
    assert settings.ingest_sources_path.endswith("ingest-sources.json")


def _source(**overrides: Any) -> IngestSourceConfig:
    payload = {
        "source_application_id": "standalone-example",
        "object_type": "device",
        "export_base_url": "http://app.test",
        "interval_seconds": 60,
        "lookback_versions": 10,
        "page_limit": 2,
        "enabled": True,
    }
    payload.update(overrides)
    return IngestSourceConfig.model_validate(payload)


@dataclass
class _QueryResult:
    row: object | None = None

    def one_or_none(self) -> object | None:
        return self.row

    def all(self) -> list[object]:
        return [] if self.row is None else [self.row]

    def scalar_one_or_none(self) -> object | None:
        return self.row


@dataclass
class _FakeSession:
    """Async session stand-in that implements execute()/result like SQLAlchemy."""

    store: _InMemoryIngestStore
    source: IngestSourceConfig
    committed: bool = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self

    async def execute(self, statement: object, params: object = None) -> _QueryResult:
        del params
        sql = str(statement)
        if "platform_core.ingest_source" in sql:
            return _QueryResult(self._source_row())
        return _QueryResult(None)

    def _source_row(self) -> SimpleNamespace:
        config = self.source
        return SimpleNamespace(
            source_application_id=config.source_application_id,
            object_type=config.object_type,
            export_base_url=config.export_base_url,
            interval_seconds=config.interval_seconds,
            lookback_versions=config.lookback_versions,
            page_limit=config.page_limit,
            enabled=config.enabled,
            transport_mode=config.transport_mode,
            push_protocol_version=config.push_protocol_version,
            contract_validation_mode=config.contract_validation_mode,
            allow_empty_full=config.allow_empty_full,
            updated_at=datetime.now(UTC),
        )


@dataclass
class _SessionFactory:
    store: _InMemoryIngestStore
    source: IngestSourceConfig = field(default_factory=_source)

    def __call__(self) -> _FakeSession:
        return _FakeSession(store=self.store, source=self.source)


@dataclass
class _InMemoryIngestStore:
    cursor: dict[tuple[str, str], int] = field(default_factory=dict[tuple[str, str], int])
    cursor_status: dict[tuple[str, str], str] = field(
        default_factory=dict[tuple[str, str], str]
    )
    batches: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    records: list[IngestRecord] = field(default_factory=list[IngestRecord])
    fail_next_load: bool = False

    async def get_cursor(
        self,
        session: object,
        *,
        source_application_id: str,
        object_type: str,
    ) -> int:
        return self.cursor.get((source_application_id, object_type), 0)

    async def advance_cursor(
        self,
        session: object,
        *,
        source_application_id: str,
        object_type: str,
        last_version: int,
        status: str = "ok",
    ) -> None:
        key = (source_application_id, object_type)
        current = self.cursor.get(key, 0)
        if last_version >= current:
            self.cursor[key] = last_version
        self.cursor_status[key] = status

    async def load_batch(
        self,
        session: object,
        *,
        source_application_id: str,
        object_type: str,
        sync_mode: str,
        records: list[IngestRecord],
        high_watermark: int,
        payload_contract_version: str,
        from_version: int | None = None,
        **kwargs: object,
    ) -> Any:
        if self.fail_next_load:
            self.fail_next_load = False
            raise RuntimeError("simulated load failure")
        if high_watermark < max((r.version for r in records), default=0):
            raise IngestValidationError("high_watermark too low")
        self.records.extend(records)
        batch_id = uuid4()
        self.batches.append(
            {
                "batch_id": batch_id,
                "sync_mode": sync_mode,
                "high_watermark": high_watermark,
                "from_version": from_version,
                "payload_contract_version": payload_contract_version,
                "record_count": len(records),
                "source_application_id": source_application_id,
                "object_type": object_type,
                "schema_fingerprint": kwargs.get("schema_fingerprint"),
                "audit_summary": kwargs.get("audit_summary"),
            }
        )

        @dataclass
        class _Result:
            batch_id: UUID
            tombstones: int = 0

        return _Result(batch_id=batch_id)

    async def record_failed_batch(
        self,
        session: object,
        *,
        source_application_id: str,
        object_type: str,
        sync_mode: str,
        from_version: int | None,
        error: str,
    ) -> UUID:
        batch_id = uuid4()
        self.batches.append(
            {
                "batch_id": batch_id,
                "sync_mode": sync_mode,
                "status": "failed",
                "from_version": from_version,
                "error": error,
                "source_application_id": source_application_id,
                "object_type": object_type,
            }
        )
        return batch_id


@pytest.mark.asyncio
async def test_export_client_fetches_page_with_bearer_and_scope_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object_type": "device",
                "payload_contract_version": "device.v1",
                "records": [
                    {
                        "object_id": "E-1",
                        "operation": "upsert",
                        "version": 5,
                        "payload": {"name": "a"},
                    }
                ],
                "has_more": False,
                "high_watermark": 5,
            },
        )

    async def token_provider() -> str:
        return "test-token"

    client = ExportClient(
        token_provider=token_provider,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    page = await client.fetch_page(
        export_base_url="http://app.test",
        object_type="device",
        since_version=0,
        limit=100,
    )
    assert page.high_watermark == 5
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].url.params["object_type"] == "device"
    assert EXPORT_SCOPE == "ai_hub.ingest.export"
    assert EXPORT_TOKEN_SCOPES == ("ai_hub.identity", "ai_hub.ingest.export")
    await client.close()


@pytest.mark.asyncio
async def test_scheduler_full_then_incremental_with_lookback() -> None:
    store = _InMemoryIngestStore()
    pages = {
        0: {
            "object_type": "device",
            "payload_contract_version": "device.v1",
            "records": [
                {
                    "object_id": "E-1",
                    "operation": "upsert",
                    "version": 1,
                    "payload": {"name": "a"},
                },
                {
                    "object_id": "E-2",
                    "operation": "upsert",
                    "version": 2,
                    "payload": {"name": "b"},
                },
            ],
            "has_more": False,
            "high_watermark": 2,
        },
        # Incremental with lookback from cursor 2 and margin 10 → since=0.
        # Replays 1..2 (idempotent) plus new version 3; also recovers inverted 2.5→3.
    }

    def handler(request: httpx.Request) -> httpx.Response:
        since = int(request.url.params["since_version"])
        if since == 0 and store.cursor.get(("standalone-example", "device"), 0) == 0:
            return httpx.Response(200, json=pages[0])
        # After baseline, lookback re-pulls and includes late-arriving version 2
        # that would have been missed without the safety window, plus version 3.
        return httpx.Response(
            200,
            json={
                "object_type": "device",
                "payload_contract_version": "device.v1",
                "records": [
                    {
                        "object_id": "E-2",
                        "operation": "upsert",
                        "version": 2,
                        "payload": {"name": "b-late"},
                    },
                    {
                        "object_id": "E-3",
                        "operation": "upsert",
                        "version": 3,
                        "payload": {"name": "c"},
                    },
                ],
                "has_more": False,
                "high_watermark": 3,
            },
        )

    async def token_provider() -> str:
        return "tok"

    export_client = ExportClient(
        token_provider=token_provider,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    scheduler = IngestScheduler(
        RawWorkerSettings(),
        sources=[_source(lookback_versions=10)],
        sessions=_SessionFactory(store),  # type: ignore[arg-type]
        export_client=export_client,
        service=store,  # type: ignore[arg-type]
    )

    first = await scheduler.sync_source(_source(lookback_versions=10))
    assert first["sync_mode"] == "full"
    assert store.cursor[("standalone-example", "device")] == 2
    assert store.cursor_status[("standalone-example", "device")] == "ok"

    second = await scheduler.sync_source(_source(lookback_versions=10))
    assert second["sync_mode"] == "incremental"
    assert second["since_version"] == 0  # 2 - 10 floored
    assert store.cursor[("standalone-example", "device")] == 3
    assert any(record.object_id == "E-3" for record in store.records)
    await export_client.close()


@pytest.mark.asyncio
async def test_scheduler_failure_does_not_advance_cursor() -> None:
    store = _InMemoryIngestStore()
    store.cursor[("standalone-example", "device")] = 10
    store.fail_next_load = True

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object_type": "device",
                "payload_contract_version": "device.v1",
                "records": [
                    {
                        "object_id": "E-9",
                        "operation": "upsert",
                        "version": 11,
                        "payload": {"name": "x"},
                    }
                ],
                "has_more": False,
                "high_watermark": 11,
            },
        )

    async def token_provider() -> str:
        return "tok"

    export_client = ExportClient(
        token_provider=token_provider,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    scheduler = IngestScheduler(
        RawWorkerSettings(),
        sources=[_source()],
        sessions=_SessionFactory(store),  # type: ignore[arg-type]
        export_client=export_client,
        service=store,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="simulated load failure"):
        await scheduler.sync_source(_source(), force_full=False)
    assert store.cursor[("standalone-example", "device")] == 10
    assert store.cursor_status[("standalone-example", "device")] == "failed"
    assert any(batch.get("status") == "failed" for batch in store.batches)
    assert scheduler.metrics.sync_failed == 1
    await export_client.close()


@pytest.mark.asyncio
async def test_export_client_rejects_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    client = ExportClient(
        token_provider=lambda: _async_token(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ExportClientError, match="HTTP 403"):
        await client.fetch_page(
            export_base_url="http://app.test",
            object_type="device",
            since_version=0,
            limit=10,
        )
    await client.close()


async def _async_token() -> str:
    return "tok"


def test_ingest_sources_document_model() -> None:
    document = IngestSourcesDocument.model_validate(
        {
            "schema_version": 1,
            "sources": [
                {
                    "source_application_id": "app-a",
                    "object_type": "order",
                    "export_base_url": "http://app-a:9000/",
                }
            ],
        }
    )
    assert document.sources[0].export_base_url == "http://app-a:9000"


@pytest.mark.asyncio
async def test_full_enforce_aborts_before_load_when_a_page_fails_contract() -> None:
    from ai_hub_platform.modules.ingest.contract import IngestContractValidator

    store = _InMemoryIngestStore()
    pages = [
        {
            "object_type": "device",
            "payload_contract_version": "device.v1",
            "records": [
                {
                    "object_id": "E-1",
                    "operation": "upsert",
                    "version": 1,
                    "payload": {"name": "ok"},
                }
            ],
            "has_more": True,
            "high_watermark": 1,
        },
        {
            "object_type": "device",
            "payload_contract_version": "device.v1",
            "records": [
                {
                    "object_id": "E-2",
                    "operation": "upsert",
                    "version": 2,
                    "payload": {"name": "ok", "secret": True},
                }
            ],
            "has_more": False,
            "high_watermark": 2,
        },
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[min(calls["n"], 1)]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    from ai_hub_platform.modules.ingest.contract import (
        RegisteredContract,
        schema_fingerprint,
    )

    contract = RegisteredContract(
        source_application_id="standalone-example",
        object_type="device",
        contract_version="device.v1",
        json_schema=schema,
        schema_fingerprint=schema_fingerprint(schema),
        status="ACTIVE",
    )
    export_client = ExportClient(
        token_provider=lambda: _async_token(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    scheduler = IngestScheduler(
        RawWorkerSettings(ingest_pull_contract_enforcement_enabled=True),
        sources=[_source(contract_validation_mode="ENFORCE")],
        sessions=_SessionFactory(
            store, source=_source(contract_validation_mode="ENFORCE")
        ),  # type: ignore[arg-type]
        export_client=export_client,
        service=store,  # type: ignore[arg-type]
        contract_validator=IngestContractValidator(),
        contract_lookup=lambda source, version: contract,
    )
    with pytest.raises(Exception, match="ENFORCE"):
        await scheduler.sync_source(_source(contract_validation_mode="ENFORCE"), force_full=True)
    assert store.records == []
    await export_client.close()


@pytest.mark.asyncio
async def test_scheduler_persists_active_contract_fingerprint_on_pull_batches() -> None:
    store = _InMemoryIngestStore()
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    from ai_hub_platform.modules.ingest.contract import (
        IngestContractValidator,
        RegisteredContract,
        schema_fingerprint,
    )

    fingerprint = schema_fingerprint(schema)
    contract = RegisteredContract(
        source_application_id="standalone-example",
        object_type="device",
        contract_version="device.v1",
        json_schema=schema,
        schema_fingerprint=fingerprint,
        status="ACTIVE",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "object_type": "device",
                "payload_contract_version": "device.v1",
                "records": [
                    {
                        "object_id": "E-1",
                        "operation": "upsert",
                        "version": 1,
                        "payload": {"name": "a", "extra": True},
                    }
                ],
                "has_more": False,
                "high_watermark": 1,
            },
        )

    export_client = ExportClient(
        token_provider=lambda: _async_token(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    scheduler = IngestScheduler(
        RawWorkerSettings(),
        sources=[_source()],
        sessions=_SessionFactory(store),  # type: ignore[arg-type]
        export_client=export_client,
        service=store,  # type: ignore[arg-type]
        contract_validator=IngestContractValidator(),
        contract_lookup=lambda source, version: contract,
    )
    await scheduler.sync_source(_source(), force_full=True)
    assert store.batches[0]["schema_fingerprint"] == fingerprint
    summary = store.batches[0]["audit_summary"]
    assert isinstance(summary, dict)
    assert summary["mode"] == "AUDIT_ONLY"
    assert scheduler.metrics.contract_audit_issues >= 1
    await scheduler.sync_source(_source(), force_full=False)
    assert store.batches[-1]["schema_fingerprint"] == fingerprint
    await export_client.close()


def test_runtime_constructors_wire_contract_validator() -> None:
    import inspect as inspect_mod

    from ai_hub_platform.modules.ingest import rebuild as rebuild_mod
    from ai_hub_platform.modules.ingest.scheduler import run_ingest_scheduler

    assert "create_runtime_scheduler" in inspect_mod.getsource(run_ingest_scheduler)
    assert "create_runtime_scheduler" in inspect_mod.getsource(
        rebuild_mod.sync_configured_source
    )


@pytest.mark.asyncio
async def test_reload_sources_refreshes_payload_max_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest.config_store import IngestPolicy

    store = _InMemoryIngestStore()
    export_client = ExportClient(token_provider=_async_token)
    scheduler = IngestScheduler(
        RawWorkerSettings(),
        sources=[_source()],
        sessions=_SessionFactory(store),  # type: ignore[arg-type]
        export_client=export_client,
        service=store,  # type: ignore[arg-type]
        payload_max_bytes=1_048_576,
    )

    async def fake_get_policy(self: object, session: object) -> IngestPolicy:
        del self, session
        return IngestPolicy(
            retention_keep_versions=10,
            retention_keep_days=None,
            payload_max_bytes=2048,
            page_limit_default=100,
            page_limit_max=1000,
            scheduled_reconcile_enabled=False,
            reconcile_interval_hours=24,
            push_staging_retention_hours=24,
            updated_at=datetime.now(UTC),
        )

    monkeypatch.setattr(
        "ai_hub_platform.modules.ingest.scheduler.IngestConfigStore.get_policy",
        fake_get_policy,
    )

    async def loader() -> list[IngestSourceConfig]:
        return [_source()]

    await scheduler.reload_sources(loader)
    assert scheduler.payload_max_bytes == 2048
    await export_client.close()


class _RecordingReconcileService(IngestReconcileService):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def reconcile(
        self,
        session: AsyncSession,
        *,
        source_application_id: str,
        object_type: str,
    ) -> ReconcileReport:
        del session
        self.calls.append((source_application_id, object_type))
        return ReconcileReport(
            source_application_id=source_application_id,
            object_type=object_type,
            expected_count=0,
            actual_count=0,
            drifted=False,
            drifts=(),
        )


@pytest.mark.asyncio
async def test_scheduled_reconcile_covers_enabled_pull_and_push_sources() -> None:
    store = _InMemoryIngestStore()
    export_client = ExportClient(token_provider=_async_token)
    reconcile_service = _RecordingReconcileService()
    pull = _source()
    push = _source(
        object_type="event",
        transport_mode="PUSH_AGENT",
        export_base_url=None,
        push_protocol_version="1",
        contract_validation_mode="ENFORCE",
    )
    scheduler = IngestScheduler(
        RawWorkerSettings(),
        sources=[pull, push],
        sessions=_SessionFactory(store),  # type: ignore[arg-type]
        export_client=export_client,
        service=store,  # type: ignore[arg-type]
        scheduled_reconcile_enabled=True,
        reconcile_interval_hours=2,
        reconcile_service=reconcile_service,
    )

    await scheduler._run_scheduled_reconcile()  # pyright: ignore[reportPrivateUsage]

    assert reconcile_service.calls == [
        ("standalone-example", "device"),
        ("standalone-example", "event"),
    ]
    assert scheduler.metrics.reconcile_started == 2
    assert scheduler.metrics.reconcile_succeeded == 2
    assert scheduler.metrics.reconcile_failed == 0
    await export_client.close()


def test_ingest_source_request_uses_policy_default_when_page_limit_is_omitted() -> None:
    from ai_hub_platform.api.ingest import IngestSourceUpsertRequest

    request = IngestSourceUpsertRequest.model_validate(
        {
            "source_application_id": "source-app",
            "object_type": "device",
            "export_base_url": "https://source.example/export",
        }
    )
    assert request.page_limit is None


def test_ingest_policy_api_enforces_documented_page_hard_limit() -> None:
    from ai_hub_platform.api.ingest import IngestPolicyUpdateRequest

    with pytest.raises(ValidationError, match="less than or equal to 5000"):
        IngestPolicyUpdateRequest.model_validate(
            {
                "retention_keep_versions": 100,
                "payload_max_bytes": 1048576,
                "page_limit_default": 200,
                "page_limit_max": 5001,
                "scheduled_reconcile_enabled": False,
                "reconcile_interval_hours": 24,
            }
        )


@pytest.mark.asyncio
async def test_scheduler_aborts_publish_when_source_is_no_longer_pull() -> None:
    store = _InMemoryIngestStore()
    store.cursor[("standalone-example", "device")] = 10

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object_type": "device",
                "payload_contract_version": "device.v1",
                "records": [
                    {
                        "object_id": "E-9",
                        "operation": "upsert",
                        "version": 11,
                        "payload": {"name": "x"},
                    }
                ],
                "has_more": False,
                "high_watermark": 11,
            },
        )

    async def token_provider() -> str:
        return "tok"

    export_client = ExportClient(
        token_provider=token_provider,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    scheduler = IngestScheduler(
        RawWorkerSettings(),
        sources=[_source()],
        sessions=_SessionFactory(store, source=_source(enabled=False)),  # type: ignore[arg-type]
        export_client=export_client,
        service=store,  # type: ignore[arg-type]
    )
    with pytest.raises(ExportClientError, match="PULL_EXPORT"):
        await scheduler.sync_source(_source(), force_full=False)
    assert store.cursor[("standalone-example", "device")] == 10
    await export_client.close()


def test_pull_revalidates_contract_after_source_lock() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src/ai_hub_platform/modules/ingest/scheduler.py"
    ).read_text(encoding="utf-8")
    incremental = source.split("async def _run_incremental_sync", 1)[1].split(
        "async def _run_", 2
    )[0]
    full = source.split("async def _run_full_sync", 1)[1].split(
        "async def _run_incremental_sync", 1
    )[0]
    assert incremental.index("lock_ingest_source") < incremental.index(
        "_validate_locked_records"
    )
    assert full.index("lock_ingest_source") < full.index("_validate_locked_records")
    assert incremental.index("_audit_pull_page") < incremental.index(
        "lock_ingest_source"
    )
    assert full.index("_audit_pull_page") < full.index("lock_ingest_source")
    audit = source.split("async def _audit_pull_page", 1)[1].split("async def ", 1)[0]
    assert "AUDIT_ONLY" in audit
    locked = source.split("async def _validate_locked_records", 1)[1].split(
        "async def ", 1
    )[0]
    assert "session=session" in locked
    assert "self.sessions()" not in locked
    lookup = source.split("async def _lookup_contract", 1)[1].split("async def ", 1)[0]
    assert "session is not None" in lookup
    locked_source = source.split("async def _require_locked_pull_source", 1)[1].split(
        "async def ", 1
    )[0]
    assert "except AttributeError" not in locked_source


def test_policy_update_allows_omitting_push_staging_retention() -> None:
    from ai_hub_platform.api.ingest import IngestPolicyUpdateRequest

    payload = IngestPolicyUpdateRequest.model_validate(
        {
            "retention_keep_versions": 100,
            "payload_max_bytes": 1048576,
            "page_limit_default": 200,
            "page_limit_max": 5000,
            "scheduled_reconcile_enabled": False,
            "reconcile_interval_hours": 24,
        }
    )
    assert payload.push_staging_retention_hours is None
