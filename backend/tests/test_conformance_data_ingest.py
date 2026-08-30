"""Conformance coverage for DATA_INGEST runtime evidence (M7-04)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ai_hub_platform.modules.conformance.service import (
    CONTRACT_VERSION,
    ConformanceService,
    ConformanceValidationError,
)


def _pull_keys(object_type: str = "device") -> dict[str, object]:
    return {
        "export_scope_enforced": True,
        "version_monotonic": True,
        "lookback_no_loss": True,
        "delete_captured": True,
        "idempotent_replay": True,
        "payload_contract_ok": True,
        "object_type": object_type,
        "schema_fingerprint": "a" * 64,
    }


def _push_keys(object_type: str = "erp.item") -> dict[str, object]:
    return {
        "transport_mode": "PUSH_AGENT",
        "inbound_identity_enforced": True,
        "contract_registered": True,
        "batch_digest_ok": True,
        "version_monotonic": True,
        "delete_captured": True,
        "full_staging_once_published": True,
        "replay_idempotent": True,
        "payload_contract_ok": True,
        "object_type": object_type,
        "schema_fingerprint": "a" * 64,
    }


@pytest.mark.asyncio
async def test_data_ingest_evidence_requires_checklist_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConformanceService()

    async def fake_context(session: object, **kwargs: object) -> dict[str, object]:
        return {
            "application_id": "standalone-example",
            "environment": "local",
            "capabilities": ["API_CLIENT", "DATA_INGEST"],
        }

    async def fake_transports(session: object, **kwargs: object) -> list[tuple[str, str]]:
        return [("device", "PULL_EXPORT")]

    monkeypatch.setattr(service, "_application_context", fake_context)
    monkeypatch.setattr(service, "_load_ingest_transports", fake_transports)

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("should fail before insert")

    with pytest.raises(ConformanceValidationError, match="missing required keys"):
        await service.record_runtime_evidence(
            _Session(),  # type: ignore[arg-type]
            application_id="standalone-example",
            environment="local",
            contract_version=CONTRACT_VERSION,
            source="unit-test",
            profiles={
                "DATA_INGEST": {
                    "status": "PASSED",
                    "evidence": {"export_scope_enforced": True},
                }
            },
            verified_at=datetime.now(UTC),
        )

    assert CONTRACT_VERSION == "m7-conformance-0.1.0"


@pytest.mark.asyncio
async def test_data_ingest_push_evidence_requires_transport_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConformanceService()

    async def fake_context(session: object, **kwargs: object) -> dict[str, object]:
        return {
            "application_id": "e10-adapter",
            "environment": "local",
            "capabilities": ["API_CLIENT", "DATA_INGEST"],
        }

    async def fake_transports(session: object, **kwargs: object) -> list[tuple[str, str]]:
        return [("erp.item", "PUSH_AGENT")]

    monkeypatch.setattr(service, "_application_context", fake_context)
    monkeypatch.setattr(service, "_load_ingest_transports", fake_transports)

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("should fail before insert")

    with pytest.raises(ConformanceValidationError, match="missing required keys"):
        await service.record_runtime_evidence(
            _Session(),  # type: ignore[arg-type]
            application_id="e10-adapter",
            environment="local",
            contract_version=CONTRACT_VERSION,
            source="unit-test",
            profiles={
                "DATA_INGEST": {
                    "status": "PASSED",
                    "evidence": {
                        "transport_mode": "PUSH_AGENT",
                        "export_scope_enforced": True,
                    },
                }
            },
            verified_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_data_ingest_evidence_uses_registered_source_not_payload_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConformanceService()

    async def fake_context(session: object, **kwargs: object) -> dict[str, object]:
        return {
            "application_id": "e10-adapter",
            "environment": "local",
            "capabilities": ["API_CLIENT", "DATA_INGEST"],
        }

    async def fake_transports(session: object, **kwargs: object) -> list[tuple[str, str]]:
        return [("erp.item", "PUSH_AGENT")]

    monkeypatch.setattr(service, "_application_context", fake_context)
    monkeypatch.setattr(service, "_load_ingest_transports", fake_transports)

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("should fail before insert")

    with pytest.raises(ConformanceValidationError, match="missing required keys"):
        await service.record_runtime_evidence(
            _Session(),  # type: ignore[arg-type]
            application_id="e10-adapter",
            environment="local",
            contract_version=CONTRACT_VERSION,
            source="unit-test",
            profiles={"DATA_INGEST": {"status": "PASSED", "evidence": _pull_keys()}},
            verified_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_mixed_ingest_sources_require_by_source_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConformanceService()

    async def fake_context(session: object, **kwargs: object) -> dict[str, object]:
        return {
            "application_id": "factory",
            "environment": "local",
            "capabilities": ["API_CLIENT", "DATA_INGEST"],
        }

    async def fake_transports(session: object, **kwargs: object) -> list[tuple[str, str]]:
        return [("device", "PULL_EXPORT"), ("erp.item", "PUSH_AGENT")]

    monkeypatch.setattr(service, "_application_context", fake_context)
    monkeypatch.setattr(service, "_load_ingest_transports", fake_transports)

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("should fail before insert")

    with pytest.raises(ConformanceValidationError, match="by_source"):
        await service.record_runtime_evidence(
            _Session(),  # type: ignore[arg-type]
            application_id="factory",
            environment="local",
            contract_version=CONTRACT_VERSION,
            source="unit-test",
            profiles={
                "DATA_INGEST": {
                    "status": "PASSED",
                    "evidence": _pull_keys(),
                }
            },
            verified_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_runtime_check_rejects_stale_pull_evidence_after_push_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ConformanceService()

    async def fake_transports(session: object, **kwargs: object) -> list[tuple[str, str]]:
        del session, kwargs
        return [("erp.item", "PUSH_AGENT")]

    monkeypatch.setattr(service, "_load_ingest_transports", fake_transports)

    class _Result:
        def mappings(self) -> _Result:
            return self

        def one_or_none(self) -> dict[str, object]:
            now = datetime.now(UTC)
            return {
                "status": "PASSED",
                "source": "unit-test",
                "evidence": _pull_keys(),
                "evidence_sha256": "abc",
                "verified_at": now,
                "expires_at": now + timedelta(days=1),
            }

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> _Result:
            return _Result()

    result = await service._runtime_profile_check(  # pyright: ignore[reportPrivateUsage]
        _Session(),  # type: ignore[arg-type]
        {
            "application_id": "e10-adapter",
            "environment": "local",
            "capabilities": ["API_CLIENT", "DATA_INGEST"],
        },
        "DATA_INGEST",
    )
    assert result.status == "FAILED"
    assert "no longer matches" in result.message


@pytest.mark.asyncio
async def test_same_transport_multi_source_requires_by_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.modules.conformance.service import (
        unpack_data_ingest_check_evidence,
    )

    service = ConformanceService()

    async def fake_context(session: object, **kwargs: object) -> dict[str, object]:
        return {
            "application_id": "factory",
            "environment": "local",
            "capabilities": ["API_CLIENT", "DATA_INGEST"],
        }

    async def fake_transports(session: object, **kwargs: object) -> list[tuple[str, str]]:
        return [("device", "PULL_EXPORT"), ("order", "PULL_EXPORT")]

    monkeypatch.setattr(service, "_application_context", fake_context)
    monkeypatch.setattr(service, "_load_ingest_transports", fake_transports)

    class _Session:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("should fail before insert")

    with pytest.raises(ConformanceValidationError, match="by_source"):
        await service.record_runtime_evidence(
            _Session(),  # type: ignore[arg-type]
            application_id="factory",
            environment="local",
            contract_version=CONTRACT_VERSION,
            source="unit-test",
            profiles={
                "DATA_INGEST": {
                    "status": "PASSED",
                    "evidence": _pull_keys("device"),
                }
            },
            verified_at=datetime.now(UTC),
        )

    wrapper = {
        "runtime": {
            "by_source": {
                "erp.item": {
                    **_push_keys("erp.item"),
                    "certification_kind": "full_regression",
                }
            }
        },
        "expires_at": datetime.now(UTC).isoformat(),
    }
    unpacked = unpack_data_ingest_check_evidence(wrapper, "erp.item")
    assert unpacked["object_type"] == "erp.item"
    assert unpacked["certification_kind"] == "full_regression"