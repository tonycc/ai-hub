from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_hub_platform.modules.operations.service import (
    diagnose_freshness,
    diagnose_source_status,
)


def test_source_diagnostic_distinguishes_success_failure_and_disabled() -> None:
    assert diagnose_source_status(enabled=True, last_status="ok")[0] == "HEALTHY"
    assert diagnose_source_status(enabled=True, last_status="FAILED")[0] == "CRITICAL"
    assert diagnose_source_status(enabled=True, last_status=None)[0] == "UNKNOWN"
    assert diagnose_source_status(enabled=False, last_status="FAILED")[0] == "DISABLED"


def test_freshness_uses_configured_source_interval() -> None:
    observed_at = datetime(2026, 9, 1, 12, tzinfo=UTC)

    healthy = diagnose_freshness(
        observed_at=observed_at,
        enabled=True,
        interval_seconds=60,
        last_success_at=observed_at - timedelta(seconds=120),
    )
    warning = diagnose_freshness(
        observed_at=observed_at,
        enabled=True,
        interval_seconds=60,
        last_success_at=observed_at - timedelta(seconds=121),
    )
    critical = diagnose_freshness(
        observed_at=observed_at,
        enabled=True,
        interval_seconds=60,
        last_success_at=observed_at - timedelta(seconds=241),
    )
    missing = diagnose_freshness(
        observed_at=observed_at,
        enabled=True,
        interval_seconds=60,
        last_success_at=None,
    )

    assert healthy[0] == "HEALTHY"
    assert warning[0] == "WARNING"
    assert critical[0] == "CRITICAL"
    assert missing[0] == "WARNING"
