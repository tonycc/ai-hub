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
    assert targets["deployment_tier"]["profile"] == "base-access"
    assert "event_backlog_warning" not in targets["slo"]
    assert "projection_rto_minutes" not in targets["recovery"]
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


def test_base_access_backup_requires_only_base_database_roles() -> None:
    roles = set(role_names_for_profile("base-access"))
    assert "standalone_outbox_publisher" not in roles
    assert "standalone_event_consumer" not in roles
    assert "ai_hub_raw" in roles
    assert "standalone_app" in roles
    with pytest.raises(BackupError, match="Unsupported backup profile"):
        role_names_for_profile("standard-events")


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


def test_systemd_monitor_runs_each_minute_with_durable_state_and_no_public_bind() -> None:
    systemd = PROJECT_ROOT / "deploy" / "operations" / "systemd"
    service = (systemd / "ai-hub-monitor.service").read_text(encoding="utf-8")
    timer = (systemd / "ai-hub-monitor.timer").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/ai-hub/monitor.env" in service
    assert "--state-file /var/lib/ai-hub-monitor/state.json" in service
    assert "ReadOnlyPaths=/mnt/ai-hub-off-host-backups" in service
    assert "OnUnitActiveSec=60" in timer
    assert "Persistent=true" in timer
    assert '"127.0.0.1:${AI_HUB_INTERNAL_API_PORT:-18080}:8000"' in compose


def test_production_runtime_env_never_serves_localhost_launch_urls() -> None:
    """The generated production env must not let the blueprint fall back to localhost."""
    generator = (
        PROJECT_ROOT / "scripts" / "deploy" / "generate-runtime-env.sh"
    ).read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    blueprint = (PROJECT_ROOT / "deploy" / "authentik" / "ai-hub-blueprint.yaml").read_text(
        encoding="utf-8"
    )

    # The generator always emits a real HTTPS launch URL for the app host.
    assert "STANDALONE_PORTAL_URL=https://${APP_HOST}/" in generator
    # platform-api bootstrap reconciliation needs the standalone entry points
    # injected with the AI_HUB_ prefix; they must not fall back to localhost.
    assert "AI_HUB_STANDALONE_PORTAL_URL=https://${APP_HOST}/" in generator
    assert "AI_HUB_STANDALONE_API_BASE_URL=https://${APP_HOST}/api/v1" in generator
    assert "AI_HUB_STANDALONE_HEALTH_URL=https://${APP_HOST}/health/live" in generator
    assert "AI_HUB_STANDALONE_OIDC_REDIRECT_URI=https://${APP_HOST}/auth/callback" in generator
    # The admin API must use the cluster-internal Authentik endpoint in
    # production: routing bootstrap reconciliation through the public Traefik
    # address would deadlock a fresh stack (Traefik waits on platform-api
    # readiness, readiness waits on reconciliation).
    assert "AI_HUB_AUTHENTIK_API_URL=http://authentik-server:9000/api/v3" in generator
    assert "AI_HUB_AUTHENTIK_API_URL=https://${AUTH_HOST}" not in generator
    # The worker passes the value through so the blueprint env lookup resolves.
    assert "STANDALONE_PORTAL_URL: ${STANDALONE_PORTAL_URL" in compose
    # platform-api maps the four AI_HUB_STANDALONE_* settings.
    assert "AI_HUB_STANDALONE_PORTAL_URL: ${AI_HUB_STANDALONE_PORTAL_URL" in compose
    assert "AI_HUB_STANDALONE_API_BASE_URL: ${AI_HUB_STANDALONE_API_BASE_URL" in compose
    assert "AI_HUB_STANDALONE_HEALTH_URL: ${AI_HUB_STANDALONE_HEALTH_URL" in compose
    assert "AI_HUB_STANDALONE_OIDC_REDIRECT_URI: ${AI_HUB_STANDALONE_OIDC_REDIRECT_URI" in compose
    # The blueprint reads the value instead of hardcoding the callback as the
    # launch URL.
    assert "STANDALONE_PORTAL_URL" in blueprint


def test_sandbox_configuration_uses_dedicated_provider_identity() -> None:
    # The developer center copies the sandbox configuration into the reference
    # app's environment, so the issuer/audience must describe the sandbox
    # application's own dedicated provider, not the shared platform provider.
    source = (
        PROJECT_ROOT / "backend" / "src" / "ai_hub_platform" / "api" / "developer.py"
    ).read_text(encoding="utf-8")
    assert 'f"/application/o/{settings.sandbox_application_id}/"' in source
    assert "oidc_audience=settings.sandbox_application_id" in source
    assert '"/application/o/ai-hub/"' not in source
    assert "oidc_audience=settings.oidc_audience" not in source
