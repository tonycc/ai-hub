"""Conformance coverage for DATA_INGEST runtime evidence (M7-04)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ai_hub_platform.modules.conformance.service import (
    CONTRACT_VERSION,
    ConformanceService,
    ConformanceValidationError,
)


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

    monkeypatch.setattr(service, "_application_context", fake_context)

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
