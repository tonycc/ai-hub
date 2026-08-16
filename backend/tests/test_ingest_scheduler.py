"""Unit tests for M7-02 ingest scheduler, sources, and export client."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from ai_hub_platform.config import RawWorkerSettings
from ai_hub_platform.modules.ingest.export_client import (
    EXPORT_SCOPE,
    ExportClient,
    ExportClientError,
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


@dataclass
class _FakeSession:
    """Minimal async session stand-in for scheduler unit tests."""

    store: _InMemoryIngestStore
    committed: bool = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self


@dataclass
class _SessionFactory:
    store: _InMemoryIngestStore

    def __call__(self) -> _FakeSession:
        return _FakeSession(store=self.store)


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
