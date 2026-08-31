"""Transport-mode rules for DATA_INGEST Pull vs Push (ADR-033 / C1-A)."""

from __future__ import annotations

import pytest
from ai_hub_platform.modules.ingest.sources import IngestSourceConfig
from pydantic import ValidationError


def _pull(**overrides: object) -> IngestSourceConfig:
    payload: dict[str, object] = {
        "source_application_id": "standalone-example",
        "object_type": "device",
        "export_base_url": "http://app.test",
    }
    payload.update(overrides)
    return IngestSourceConfig.model_validate(payload)


def _push(**overrides: object) -> IngestSourceConfig:
    payload: dict[str, object] = {
        "source_application_id": "e10-adapter",
        "object_type": "erp.item",
        "transport_mode": "PUSH_AGENT",
        "export_base_url": None,
        "push_protocol_version": "1",
        "contract_validation_mode": "ENFORCE",
    }
    payload.update(overrides)
    return IngestSourceConfig.model_validate(payload)


def test_existing_source_defaults_to_pull_export() -> None:
    source = _pull()
    assert source.transport_mode == "PULL_EXPORT"
    assert source.contract_validation_mode == "AUDIT_ONLY"
    assert source.push_protocol_version is None
    assert source.allow_empty_full is False
    assert source.export_base_url == "http://app.test"


def test_push_agent_requires_protocol_version_and_empty_url() -> None:
    source = _push()
    assert source.transport_mode == "PUSH_AGENT"
    assert source.export_base_url is None
    assert source.push_protocol_version == "1"
    assert source.contract_validation_mode == "ENFORCE"


def test_pull_export_rejects_missing_export_url() -> None:
    with pytest.raises(ValidationError, match="export_base_url"):
        _pull(export_base_url=None)


def test_push_agent_rejects_export_url() -> None:
    with pytest.raises(ValidationError, match="export_base_url"):
        _push(export_base_url="http://app.test")


def test_push_agent_rejects_missing_protocol_version() -> None:
    with pytest.raises(ValidationError, match="push_protocol_version"):
        _push(push_protocol_version=None)


def test_pull_export_rejects_push_protocol_version() -> None:
    with pytest.raises(ValidationError, match="push_protocol_version"):
        _pull(push_protocol_version="1")


def test_push_agent_rejects_audit_only_contract_mode() -> None:
    with pytest.raises(ValidationError, match="ENFORCE"):
        _push(contract_validation_mode="AUDIT_ONLY")


def test_push_agent_normalizes_and_rejects_protocol_version() -> None:
    assert _push(push_protocol_version=" 1 ").push_protocol_version == "1"
    with pytest.raises(ValidationError, match="push_protocol_version"):
        _push(push_protocol_version="2")


def test_pull_export_sources_skips_push_agent() -> None:
    from ai_hub_platform.modules.ingest import sources as sources_mod

    pull_export_sources = getattr(sources_mod, "pull_export_sources", None)
    assert pull_export_sources is not None
    pull = _pull()
    push = _push()
    selected = pull_export_sources([push, pull, _push(object_type="erp.sales_order")])
    assert selected == [pull]


def test_source_rebuild_not_supported_is_distinct_error() -> None:
    from ai_hub_platform.modules.ingest import rebuild as rebuild_mod

    cls = getattr(rebuild_mod, "SourceRebuildNotSupported", None)
    assert cls is not None
    error = cls("e10-adapter", "erp.item")
    assert error.error_code == "source_rebuild_not_supported"
    assert "e10-adapter" in str(error)
    assert "erp.item" in str(error)


def test_upsert_sql_rewrites_transport_mode_after_quiesce() -> None:
    import inspect

    from ai_hub_platform.modules.ingest import config_store

    source = inspect.getsource(config_store.IngestConfigStore.upsert_source)
    assert "transport_mode = EXCLUDED.transport_mode" in source
    assert "CHANGE_RECORD_PURPOSE_UNIQUE" in source
    assert "IngestPushNotIsolatedError" in inspect.getsource(config_store)
    assert "existing.config.enabled or config.enabled" in source


def test_push_identity_uses_token_claim_not_header_fallback() -> None:
    import inspect

    from ai_hub_platform.api import ingest_push

    source = inspect.getsource(ingest_push)
    assert "principal.token.application_id" in source
    assert "return principal.application_id" not in source
    assert "ingest_push_change_log_not_isolated" in source
    assert "CHANGE_RECORD_PURPOSE_UNIQUE" in source


def test_put_ingest_source_rejects_active_generation_before_transport_switch() -> None:
    import inspect

    from ai_hub_platform.api import ingest as ingest_api
    from ai_hub_platform.modules.ingest.config_store import IngestTransportBusyError

    source = inspect.getsource(ingest_api.put_ingest_source)
    assert "lock_ingest_source" in source
    assert source.index("lock_ingest_source") < source.index(
        "_reject_if_active_push_generation"
    )
    assert source.index("_reject_if_active_push_generation") < source.index(
        "upsert_source"
    )
    assert "raw_sessions" not in source
    assert IngestTransportBusyError.error_code == "ingest_transport_mode_busy"


def test_ingest_scheduler_receives_pull_contract_enforcement_flag() -> None:
    from pathlib import Path

    compose = (Path(__file__).resolve().parents[2] / "deploy/compose.yaml").read_text()
    scheduler_block = compose.split("platform-ingest-scheduler:", 1)[1]
    assert "AI_HUB_INGEST_PULL_CONTRACT_ENFORCEMENT_ENABLED" in scheduler_block.split(
        "volumes:", 1
    )[0]


@pytest.mark.asyncio
async def test_pull_enforce_upsert_requires_approved_certification() -> None:
    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestEnforceNotCertifiedError,
    )

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

        def all(self) -> list[object]:
            return []

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    with pytest.raises(IngestEnforceNotCertifiedError):
        await IngestConfigStore().upsert_source(
            _Session(),  # type: ignore[arg-type]
            _pull(contract_validation_mode="ENFORCE"),
        )


@pytest.mark.asyncio
async def test_existing_source_cannot_change_transport_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestSourceRow,
        IngestTransportImmutableError,
    )

    store = IngestConfigStore()
    existing = IngestSourceRow(config=_push(), updated_at=datetime.now(UTC))

    async def fake_get_source(session: object, **kwargs: object) -> IngestSourceRow:
        del session, kwargs
        return existing

    monkeypatch.setattr(store, "get_source", fake_get_source)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    with pytest.raises(IngestTransportImmutableError):
        await store.upsert_source(
            _Session(),  # type: ignore[arg-type]
            _pull(
                source_application_id="e10-adapter",
                object_type="erp.item",
                contract_validation_mode="ENFORCE",
            ),
        )


@pytest.mark.asyncio
async def test_disabled_source_can_change_transport_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestSourceRow,
    )

    store = IngestConfigStore()
    existing = IngestSourceRow(
        config=_push(enabled=False), updated_at=datetime.now(UTC)
    )

    async def fake_get_source(session: object, **kwargs: object) -> IngestSourceRow:
        del session, kwargs
        return existing

    monkeypatch.setattr(store, "get_source", fake_get_source)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    await store.upsert_source(
        _Session(),  # type: ignore[arg-type]
        _pull(
            source_application_id="e10-adapter",
            object_type="erp.item",
            enabled=False,
        ),
    )


@pytest.mark.asyncio
async def test_disable_and_switch_transport_in_one_request_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestSourceRow,
        IngestTransportImmutableError,
    )

    store = IngestConfigStore()
    existing = IngestSourceRow(config=_push(enabled=True), updated_at=datetime.now(UTC))

    async def fake_get_source(session: object, **kwargs: object) -> IngestSourceRow:
        del session, kwargs
        return existing

    monkeypatch.setattr(store, "get_source", fake_get_source)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    with pytest.raises(IngestTransportImmutableError):
        await store.upsert_source(
            _Session(),  # type: ignore[arg-type]
            _pull(
                source_application_id="e10-adapter",
                object_type="erp.item",
                enabled=False,
            ),
        )


@pytest.mark.asyncio
async def test_disabled_push_to_pull_enforce_still_requires_certification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestEnforceNotCertifiedError,
        IngestSourceRow,
    )

    store = IngestConfigStore()
    existing = IngestSourceRow(
        config=_push(enabled=False), updated_at=datetime.now(UTC)
    )

    async def fake_get_source(session: object, **kwargs: object) -> IngestSourceRow:
        del session, kwargs
        return existing

    monkeypatch.setattr(store, "get_source", fake_get_source)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    with pytest.raises(IngestEnforceNotCertifiedError):
        await store.upsert_source(
            _Session(),  # type: ignore[arg-type]
            _pull(
                source_application_id="e10-adapter",
                object_type="erp.item",
                enabled=False,
                contract_validation_mode="ENFORCE",
            ),
        )


def test_change_log_purpose_unique_contract_is_active() -> None:
    from ai_hub_platform.modules.ingest.sources import CHANGE_RECORD_PURPOSE_UNIQUE

    assert CHANGE_RECORD_PURPOSE_UNIQUE is True


@pytest.mark.asyncio
async def test_enabling_push_source_requires_approved_certification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.modules.ingest import config_store as config_store_mod
    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestEnforceNotCertifiedError,
    )

    monkeypatch.setattr(config_store_mod, "CHANGE_RECORD_PURPOSE_UNIQUE", True)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

        def all(self) -> list[object]:
            return []

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    with pytest.raises(IngestEnforceNotCertifiedError, match="PUSH_AGENT"):
        await IngestConfigStore().upsert_source(
            _Session(),  # type: ignore[arg-type]
            _push(enabled=True),
        )


@pytest.mark.asyncio
async def test_re_enabling_push_source_requires_approved_certification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest import config_store as config_store_mod
    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestEnforceNotCertifiedError,
        IngestSourceRow,
    )

    monkeypatch.setattr(config_store_mod, "CHANGE_RECORD_PURPOSE_UNIQUE", True)
    store = IngestConfigStore()
    existing = IngestSourceRow(
        config=_push(enabled=False), updated_at=datetime.now(UTC)
    )

    async def fake_get_source(session: object, **kwargs: object) -> IngestSourceRow:
        del session, kwargs
        return existing

    monkeypatch.setattr(store, "get_source", fake_get_source)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    with pytest.raises(IngestEnforceNotCertifiedError, match="PUSH_AGENT"):
        await store.upsert_source(
            _Session(),  # type: ignore[arg-type]
            _push(enabled=True),
        )


@pytest.mark.asyncio
async def test_re_enabling_pull_enforce_source_requires_approved_certification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestEnforceNotCertifiedError,
        IngestSourceRow,
    )

    store = IngestConfigStore()
    existing = IngestSourceRow(
        config=_pull(enabled=False, contract_validation_mode="ENFORCE"),
        updated_at=datetime.now(UTC),
    )

    async def fake_get_source(session: object, **kwargs: object) -> IngestSourceRow:
        del session, kwargs
        return existing

    monkeypatch.setattr(store, "get_source", fake_get_source)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
            return _EmptyResult()

    with pytest.raises(IngestEnforceNotCertifiedError, match="ENFORCE"):
        await store.upsert_source(
            _Session(),  # type: ignore[arg-type]
            _pull(enabled=True, contract_validation_mode="ENFORCE"),
        )


def test_put_ingest_source_audits_contract_mode_old_and_new() -> None:
    import inspect

    from ai_hub_platform.api import ingest as ingest_api

    source = inspect.getsource(ingest_api.put_ingest_source)
    assert '"contract_validation_mode"' in source
    assert '"transport_mode"' in source
    assert "IngestPushNotIsolatedError" in source
    assert '"old"' in source
    assert '"new"' in source


def test_push_sources_use_generation_progress_not_pull_cursor() -> None:
    import inspect
    from pathlib import Path

    from ai_hub_platform.api import ingest as ingest_api
    from ai_hub_platform.modules.ingest import sources as sources_mod

    progress = inspect.getsource(sources_mod.load_push_progress)
    assert "raw_push_generation" in progress
    assert "raw_push_committed_watermark" in progress
    assert "PUSH_AGENT" in progress
    assert "generation_keys" in progress
    assert "last_success_at" in progress
    assert "purpose = 'production'" in progress
    assert "COALESCE(purpose, 'production') = 'production'" in progress
    mapped = inspect.getsource(ingest_api)
    assert "last_success_at=cursor.get(\"last_success_at\")" in mapped
    assert "last_success_at" in ingest_api.IngestSourceResponse.model_fields
    config = inspect.getsource(ingest_api.get_ingest_config)
    assert "load_push_progress" in config
    view = (
        Path(__file__).resolve().parents[2] / "src/views/IngestView.vue"
    ).read_text(encoding="utf-8")
    assert "isPushSource" in view
    assert "从未推送" in view
    assert "lastStatusOk" in view
    assert "lastStatusFailed" in view
    assert "last_success_at" in view
    assert "padding: var(--space-card-lg)" in view
    assert "padding: 18px 22px 8px" not in view
    layout = (
        Path(__file__).resolve().parents[2] / "src/layouts/PlatformLayout.vue"
    ).read_text(encoding="utf-8")
    assert "'/platform/ingest': 'platform.ingest.read'" in layout


def test_production_replay_and_history_exclude_certification_purpose() -> None:
    import inspect

    from ai_hub_platform.modules.ingest import query as query_mod
    from ai_hub_platform.modules.ingest import reconcile as reconcile_mod
    from ai_hub_platform.modules.ingest import service as service_mod

    change_log = inspect.getsource(reconcile_mod.IngestReconcileService.load_change_log)
    history = inspect.getsource(query_mod.DataQueryService)
    service_src = inspect.getsource(service_mod.IngestService)
    prune = inspect.getsource(reconcile_mod.prune_change_records)
    assert "COALESCE(batch.purpose, 'production') = 'production'" in change_log
    assert "COALESCE(record.purpose, 'production') = 'production'" in change_log
    assert "COALESCE(batch.purpose, 'production') = 'production'" in history
    assert "COALESCE(record.purpose, 'production') = 'production'" in history
    assert "begin_nested" in service_src
    insert = service_src.split("async def _insert_change_record", 1)[1].split(
        "async def ", 1
    )[0]
    conflict = insert.split("ON CONFLICT", 1)[1].split("DO NOTHING", 1)[0]
    assert "purpose" in conflict
    assert "AND purpose = :purpose" in insert
    assert "already exists with a different purpose" not in insert
    assert "PARTITION BY source_application_id, object_type, object_id, purpose" in prune


@pytest.mark.asyncio
async def test_push_enable_requires_matching_transport_certification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    from ai_hub_platform.modules.ingest import config_store as config_store_mod
    from ai_hub_platform.modules.ingest.config_store import (
        IngestConfigStore,
        IngestEnforceNotCertifiedError,
        IngestSourceRow,
    )

    monkeypatch.setattr(config_store_mod, "CHANGE_RECORD_PURPOSE_UNIQUE", True)
    store = IngestConfigStore()
    existing = IngestSourceRow(
        config=_push(enabled=False),
        updated_at=datetime.now(UTC),
    )
    captured: dict[str, object] = {}

    async def fake_get_source(session: object, **kwargs: object) -> IngestSourceRow:
        del session, kwargs
        return existing

    monkeypatch.setattr(store, "get_source", fake_get_source)

    class _EmptyResult:
        def one_or_none(self) -> None:
            return None

    class _Session:
        async def execute(
            self, statement: object, params: object = None
        ) -> _EmptyResult:
            captured["sql"] = str(statement)
            captured["params"] = params
            return _EmptyResult()

    with pytest.raises(IngestEnforceNotCertifiedError):
        await store.upsert_source(
            _Session(),  # type: ignore[arg-type]
            _push(enabled=True),
        )
    assert "c.transport_mode" in str(captured["sql"])
    assert isinstance(captured["params"], dict)
    assert captured["params"]["transport_mode"] == "PUSH_AGENT"


@pytest.mark.asyncio
async def test_push_progress_keeps_generation_time_when_batch_is_older() -> None:
    from datetime import UTC, datetime, timedelta

    from ai_hub_platform.modules.ingest.sources import load_push_progress

    gen_time = datetime(2026, 8, 30, 12, tzinfo=UTC)
    batch_time = gen_time - timedelta(hours=5)

    class _Gen:
        source_application_id = "e10-adapter"
        object_type = "erp.item"
        status = "FAILED"
        updated_at = gen_time

    class _Batch:
        source_application_id = "e10-adapter"
        object_type = "erp.item"
        status = "loaded"
        last_at = batch_time

    class _Rows:
        def __init__(self, rows: list[object]) -> None:
            self._rows = rows

        def all(self) -> list[object]:
            return self._rows

    calls = {"n": 0}

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Rows:
            del args, kwargs
            calls["n"] += 1
            if calls["n"] == 1:
                return _Rows([_Gen()])
            if calls["n"] == 2:
                return _Rows([])
            return _Rows([_Batch()])

    progress = await load_push_progress(_Session())  # type: ignore[arg-type]
    entry = progress[("e10-adapter", "erp.item")]
    assert entry["last_status"] == "FAILED"
    assert entry["last_sync_at"] == gen_time
    assert entry["last_success_at"] == batch_time


def test_enable_gate_matches_certification_transport_mode() -> None:
    import inspect

    from ai_hub_platform.modules.ingest.config_store import IngestConfigStore

    source = inspect.getsource(IngestConfigStore)
    assert "c.transport_mode" in source
    assert ":transport_mode" in source
