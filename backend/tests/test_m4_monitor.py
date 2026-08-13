from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from ai_hub_platform.operations.monitor import (
    AlertState,
    MonitorError,
    backup_age_minutes,
    evaluate_observations,
    probe_application_entries,
    rules_by_id,
    run_check,
    transition_alerts,
)
from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_alert_rules() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    directory = PROJECT_ROOT / "deploy" / "operations"
    config = cast(
        dict[str, Any],
        json.loads((directory / "alert-rules.json").read_text(encoding="utf-8")),
    )
    schema = json.loads((directory / "alert-rules.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(config)  # pyright: ignore[reportUnknownMemberType]
    return config, rules_by_id(config)


def load_targets() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            (PROJECT_ROOT / "deploy/operations/production-targets.json").read_text(
                encoding="utf-8"
            )
        ),
    )


def test_alert_rules_are_schema_valid_and_route_to_approved_owners() -> None:
    config, rules = load_alert_rules()
    targets = load_targets()

    assert len(rules) == len(config["rules"])
    assert {rule["route"] for rule in rules.values()} <= set(targets["alert_routes"])
    assert {rule["severity"] for rule in rules.values()} == {"P0", "P1", "P2"}


def test_monitor_evaluates_readiness_backlog_consumer_gap_and_backup() -> None:
    _, rules = load_alert_rules()
    observations = evaluate_observations(
        rules,
        readiness=False,
        readiness_request_id="request-1",
        http_probes={"identity-unready": False, "portal-unready": True},
        operations={
            "application_entries": [
                {
                    "application_id": "crm",
                    "environment": "production",
                    "status": "CRITICAL",
                    "reason": "HTTP 503",
                }
            ],
            "event_queues": [
                {
                    "queue_name": "ai-hub.crm",
                    "messages_ready": 1001,
                    "messages_unacknowledged": 1,
                    "consumer_count": 0,
                }
            ],
            "projections": [
                {"application_id": "crm", "open_gap_count": 1}
            ],
        },
        backup_age_minutes=61,
        targets=load_targets(),
    )
    active = {(item.rule_id, item.object_id) for item in observations if item.active}

    assert ("platform-api-unready", "ai-hub-platform") in active
    assert ("identity-unready", "identity") in active
    assert ("application-entry-critical", "crm:production") in active
    assert ("event-consumer-missing", "ai-hub.crm") in active
    assert ("event-backlog-critical", "ai-hub.crm") in active
    assert ("projection-gap-open", "crm") in active
    assert ("backup-rpo-breached", "off-host-backup") in active
    assert ("event-backlog-warning", "ai-hub.crm") not in active


def test_monitor_honors_for_period_deduplicates_and_emits_recovery() -> None:
    _, rules = load_alert_rules()
    observed_at = datetime(2026, 8, 13, 12, tzinfo=UTC)
    active = evaluate_observations(
        rules,
        readiness=False,
        readiness_request_id="request-1",
        http_probes={"identity-unready": True, "portal-unready": True},
        operations={
            "application_entries": [],
            "event_queues": [],
            "projections": [],
        },
        backup_age_minutes=1,
        targets=load_targets(),
    )

    pending, pending_notifications = transition_alerts(
        active,
        {},
        rules,
        now=observed_at,
    )
    readiness_fingerprint = next(
        item.fingerprint for item in active if item.rule_id == "platform-api-unready"
    )
    assert pending[readiness_fingerprint].status == "PENDING"
    assert pending_notifications == []

    firing, firing_notifications = transition_alerts(
        active,
        pending,
        rules,
        now=observed_at + timedelta(seconds=61),
    )
    assert firing[readiness_fingerprint].status == "FIRING"
    assert [item["status"] for item in firing_notifications] == ["FIRING"]

    still_firing, duplicates = transition_alerts(
        active,
        firing,
        rules,
        now=observed_at + timedelta(seconds=120),
    )
    assert duplicates == []

    recovered_observations = evaluate_observations(
        rules,
        readiness=True,
        readiness_request_id="request-2",
        http_probes={"identity-unready": True, "portal-unready": True},
        operations={
            "application_entries": [],
            "event_queues": [],
            "projections": [],
        },
        backup_age_minutes=1,
        targets=load_targets(),
    )
    recovered, recovery_notifications = transition_alerts(
        recovered_observations,
        still_firing,
        rules,
        now=observed_at + timedelta(seconds=180),
    )
    assert recovered[readiness_fingerprint].status == "RECOVERED"
    assert [item["status"] for item in recovery_notifications] == ["RECOVERED"]

    recurring, recurring_notifications = transition_alerts(
        active,
        recovered,
        rules,
        now=observed_at + timedelta(seconds=181),
    )
    assert recurring[readiness_fingerprint].status == "PENDING"
    assert recurring_notifications == []


def test_alert_state_never_contains_secret_or_response_payload_fields() -> None:
    fields = set(AlertState.__dataclass_fields__)
    assert fields == {
        "first_observed_at",
        "last_observed_at",
        "status",
        "notified_status",
    }


def test_monitor_actively_probes_registered_application_health() -> None:
    operations: dict[str, Any] = {
        "application_entries": [
            {
                "application_id": "healthy-app",
                "environment_status": "ACTIVE",
                "health_url": "https://healthy.test/health/live",
                "status": "UNKNOWN",
                "reason": "stale",
            },
            {
                "application_id": "failed-app",
                "environment_status": "ACTIVE",
                "health_url": "https://failed.test/health/live",
                "status": "HEALTHY",
                "reason": "stale",
            },
            {
                "application_id": "disabled-app",
                "environment_status": "DISABLED",
                "health_url": "https://disabled.test/health/live",
                "status": "DISABLED",
                "reason": "disabled",
            },
        ]
    }

    def probe_result(url: str) -> bool:
        return "healthy.test" in url

    with patch(
        "ai_hub_platform.operations.monitor._probe_url",
        side_effect=probe_result,
    ) as probe:
        probe_application_entries(operations)

    entries = cast(list[dict[str, Any]], operations["application_entries"])
    assert entries[0]["status"] == "HEALTHY"
    assert entries[1]["status"] == "CRITICAL"
    assert entries[2]["status"] == "DISABLED"
    assert probe.call_count == 2


def test_health_probes_use_a_proxy_free_http_opener() -> None:
    from ai_hub_platform.operations.monitor import DIRECT_HTTP_OPENER

    operations: dict[str, Any] = {
        "application_entries": [
            {
                "application_id": "direct-app",
                "environment_status": "ACTIVE",
                "health_url": "http://app.internal/health/live",
                "status": "UNKNOWN",
            }
        ]
    }

    class FakeResponse:
        status = 200

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    with (
        patch.object(DIRECT_HTTP_OPENER, "open", return_value=FakeResponse()) as direct,
        patch("ai_hub_platform.operations.monitor.urlopen") as environment_opener,
    ):
        probe_application_entries(operations)

    assert cast(list[dict[str, Any]], operations["application_entries"])[0][
        "status"
    ] == "HEALTHY"
    direct.assert_called_once()
    environment_opener.assert_not_called()


def test_monitor_still_routes_readiness_when_summary_is_unavailable(tmp_path: Path) -> None:
    rules_path = PROJECT_ROOT / "deploy/operations/alert-rules.json"
    targets_path = PROJECT_ROOT / "deploy/operations/production-targets.json"
    backup = tmp_path / "ai-hub-backup-20260813T120000Z-deadbeef.tar.aesgcm"
    backup.write_bytes(b"verified")
    backup.with_suffix(backup.suffix + ".sha256").write_text(
        f"{hashlib.sha256(backup.read_bytes()).hexdigest()}  {backup.name}\n",
        encoding="utf-8",
    )
    backup.with_suffix(backup.suffix + ".verified.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verified": True,
                "archive": backup.name,
                "archive_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                "backup_id": backup.name.removesuffix(".tar.aesgcm"),
                "created_at": "2026-08-13T12:00:00+00:00",
                "verified_at": "2026-08-13T12:01:00+00:00",
                "storage_class": "off-host",
                "profile": "standard-events",
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        rules=str(rules_path),
        targets=str(targets_path),
        readiness_url="http://unavailable/health/ready",
        operations_url="http://unavailable/internal/operations/summary",
        backup_directory=str(tmp_path),
        state_file=str(tmp_path / "state.json"),
        edge_base_url="http://127.0.0.1:8088",
        identity_host="auth.localhost",
        platform_host="platform.localhost",
    )

    with (
        patch.dict(
            "os.environ",
            {
                "AI_HUB_MONITOR_TOKEN": "monitor-token",
                "AI_HUB_ALERT_WEBHOOK_URL": "https://alerts.test",
            },
            clear=False,
        ),
        patch(
            "ai_hub_platform.operations.monitor._request_ready",
            return_value=(False, None),
        ),
        patch(
            "ai_hub_platform.operations.monitor._request_json",
            side_effect=MonitorError("unavailable"),
        ),
        patch(
            "ai_hub_platform.operations.monitor._probe_http",
            return_value=True,
        ),
        patch("ai_hub_platform.operations.monitor._send_webhook") as send,
        patch(
            "ai_hub_platform.operations.monitor.datetime",
            wraps=datetime,
        ) as clock,
    ):
        clock.now.return_value = datetime(2026, 8, 13, 12, 30, tzinfo=UTC)
        result = run_check(args)

    assert result["pending_count"] == 1
    send.assert_not_called()


def test_backup_freshness_requires_matching_full_verification_receipt(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 13, 12, 30, tzinfo=UTC)
    backup = tmp_path / "ai-hub-backup-20260813T120000Z-deadbeef.tar.aesgcm"
    backup.write_bytes(b"encrypted-archive")
    archive_sha256 = hashlib.sha256(backup.read_bytes()).hexdigest()
    backup.with_suffix(backup.suffix + ".sha256").write_text(
        f"{archive_sha256}  {backup.name}\n",
        encoding="utf-8",
    )
    receipt = backup.with_suffix(backup.suffix + ".verified.json")
    receipt.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verified": True,
                "archive": backup.name,
                "archive_sha256": archive_sha256,
                "backup_id": backup.name.removesuffix(".tar.aesgcm"),
                "created_at": "2026-08-13T12:00:00+00:00",
                "verified_at": "2026-08-13T12:01:00+00:00",
                "storage_class": "off-host",
                "profile": "standard-events",
            }
        ),
        encoding="utf-8",
    )

    assert backup_age_minutes(tmp_path, now) == 30

    tampered = json.loads(receipt.read_text(encoding="utf-8"))
    tampered["archive_sha256"] = "0" * 64
    receipt.write_text(json.dumps(tampered), encoding="utf-8")
    assert backup_age_minutes(tmp_path, now) is None
