from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ai_hub_platform.operations.backup import (
    BackupError,
    decrypt_file,
    encrypt_file,
    retention_selection,
    role_names_for_profile,
)
from jsonschema import Draft202012Validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_production_targets_match_their_schema_and_safety_invariants() -> None:
    operations = PROJECT_ROOT / "deploy" / "operations"
    targets = json.loads((operations / "production-targets.json").read_text(encoding="utf-8"))
    schema = json.loads(
        (operations / "production-targets.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(targets)  # pyright: ignore[reportUnknownMemberType]
    assert targets["recovery"]["backup_interval_minutes"] <= targets["recovery"][
        "rpo_minutes"
    ]
    assert targets["slo"]["public_api_p95_ms"] <= targets["slo"]["public_api_p99_ms"]
    assert targets["slo"]["event_backlog_warning"] < targets["slo"][
        "event_backlog_critical"
    ]
    assert targets["deployment_tier"]["off_host_backup_required"] is True


def test_backup_encryption_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    source = tmp_path / "source.tar"
    encrypted = tmp_path / "backup.tar.aesgcm"
    restored = tmp_path / "restored.tar"
    key = bytes(range(32))
    source.write_bytes((b"production-backup-evidence\0" * 100_000) + b"tail")

    encrypt_file(source, encrypted, key)
    decrypt_file(encrypted, restored, key)
    assert restored.read_bytes() == source.read_bytes()

    tampered = bytearray(encrypted.read_bytes())
    tampered[len(tampered) // 2] ^= 1
    encrypted.write_bytes(tampered)
    with pytest.raises(BackupError, match="authentication failed"):
        decrypt_file(encrypted, restored, key)
    assert not restored.exists()


def test_retention_keeps_hourly_and_daily_recovery_points() -> None:
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    backups = [
        (Path(f"backup-{hours}.aesgcm"), now - timedelta(hours=hours))
        for hours in range(72)
    ]

    keep, delete = retention_selection(
        backups,
        hourly_count=24,
        daily_days=3,
        now=now,
    )

    assert {Path(f"backup-{hours}.aesgcm") for hours in range(24)} <= keep
    assert Path("backup-37.aesgcm") in keep
    assert Path("backup-61.aesgcm") in keep
    assert keep.isdisjoint(delete)
    assert keep | delete == {path for path, _ in backups}


def test_standard_events_backup_requires_its_dynamic_database_roles() -> None:
    assert set(role_names_for_profile("standard-events")) - set(
        role_names_for_profile("base-access")
    ) == {"standalone_outbox_publisher", "standalone_event_consumer"}


def test_systemd_backup_uses_off_host_storage_and_persistent_timers() -> None:
    systemd = PROJECT_ROOT / "deploy" / "operations" / "systemd"
    backup_service = (systemd / "ai-hub-backup.service").read_text(encoding="utf-8")
    backup_timer = (systemd / "ai-hub-backup.timer").read_text(encoding="utf-8")
    prune_timer = (systemd / "ai-hub-backup-prune.timer").read_text(encoding="utf-8")

    assert "--storage-class off-host" in backup_service
    assert "/mnt/ai-hub-off-host-backups" in backup_service
    assert "EnvironmentFile=/etc/ai-hub/backup.env" in backup_service
    assert "OnCalendar=hourly" in backup_timer
    assert "Persistent=true" in backup_timer
    assert "Persistent=true" in prune_timer
