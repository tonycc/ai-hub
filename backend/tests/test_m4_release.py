from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from ai_hub_platform.operations.release import (
    LIVE_DATA_CONDITION_CREDENTIALS,
    LIVE_DATA_CONDITION_NO_ENFORCE_PULL,
    LIVE_DATA_CONDITION_NO_PUSH_SOURCES,
    RELEASE_SCHEMA_VERSION,
    ReleaseError,
    ReleaseTarget,
    assert_image_rollback_declared,
    assert_manifest_image_rollback_allowed,
    assert_pending_contract_writers_stopped,
    format_live_data_condition,
    live_data_conditions_for_rollback,
    migration_heads,
    parse_live_data_conditions,
    validate_backup_receipt,
    validate_migration_transition,
    validate_release_manifest,
)
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = PROJECT_ROOT / "deploy" / "operations"


def _manifest(*, environment: str = "production") -> dict[str, Any]:
    digest = "a" * 64
    gates = [
        "python",
        "frontend",
        "deployment",
        "identity-runtime",
        "recovery-runtime",
        "observability-runtime",
        "credential-rotation-runtime",
    ]
    migration_entries: dict[str, dict[str, Any]] = {
        component: {
            "component": component,
            "previous_head": f"previous-{component}",
            "target_head": f"target-{component}",
            "revisions": [],
            "phases": [],
            "rollback_schema_compatible": True,
        }
        for component in ("core", "raw")
    }
    return {
        "$schema": "../operations/release-manifest.schema.json",
        "schema_version": RELEASE_SCHEMA_VERSION,
        "release_id": "release-2026.08.14",
        "status": "APPROVED",
        "created_at": "2026-08-14T10:00:00+00:00",
        "source": {"commit_sha": "b" * 40, "dirty": False},
        "deployment": {
            "environment": environment,
            "tier": "STANDARD_SINGLE_NODE",
            "profile": "base-access",
        },
        "images": {
            "platform": f"registry.example.test/ai-hub/platform:2026.08.14@sha256:{digest}",
            "portal": f"registry.example.test/ai-hub/portal:2026.08.14@sha256:{digest}",
        },
        "component_lock": {"lock_id": "lock-1", "sha256": digest},
        "migrations": migration_entries,
        "contracts": {
            "contracts/api/platform-api.openapi.yaml": digest,
        },
        "backup": {
            "backup_id": "ai-hub-backup-20260814T095000Z-deadbeef",
            "receipt": "/mnt/backups/backup.verified.json",
            "archive_sha256": digest,
            "created_at": "2026-08-14T09:50:00+00:00",
            "verified_at": "2026-08-14T09:55:00+00:00",
            "storage_class": "off-host",
            "profile": "base-access",
        },
        "gates": [
            {
                "id": gate,
                "status": "PASSED",
                "evidence": f"/evidence/{gate}.json",
                "evidence_sha256": digest,
            }
            for gate in gates
        ],
        "approval": {
            "approved_by": "platform-owner",
            "approved_at": "2026-08-14T10:00:00+00:00",
            "remaining_risks": [],
        },
        "rollback": {
            "previous_release_id": "release-2026.08.13",
            "previous_manifest": "/releases/release-2026.08.13.json",
            "previous_manifest_sha256": digest,
            "schema_compatible": True,
            "live_data_check_required": True,
            "live_data_condition": "no_environment_has_multiple_credential_rows",
        },
    }


def test_release_manifest_matches_schema_and_runtime_invariants() -> None:
    manifest = _manifest()
    schema = json.loads((OPERATIONS / "release-manifest.schema.json").read_text(encoding="utf-8"))

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(  # pyright: ignore[reportUnknownMemberType]
        manifest
    )
    validate_release_manifest(manifest)


def test_release_manifest_rejects_inconsistent_rollback_schema_compatible() -> None:
    manifest = _manifest()
    manifest["migrations"]["core"]["rollback_schema_compatible"] = False
    manifest["rollback"]["schema_compatible"] = True
    with pytest.raises(ReleaseError, match="rollback.schema_compatible must equal"):
        validate_release_manifest(manifest)


def test_contract_manifest_requires_risk_and_forbids_image_rollback() -> None:
    manifest = _manifest()
    manifest["migrations"]["raw"].update(
        {
            "revisions": ["20260831_raw_0007"],
            "phases": ["contract"],
            "rollback_schema_compatible": False,
        }
    )
    with pytest.raises(ReleaseError, match="document the remaining risk"):
        validate_release_manifest(manifest)

    manifest["approval"]["remaining_risks"] = [
        "Previous images cannot write after the purpose contract"
    ]
    manifest["rollback"]["schema_compatible"] = False
    manifest["rollback"]["live_data_condition"] = ";".join(
        (
            LIVE_DATA_CONDITION_CREDENTIALS,
            LIVE_DATA_CONDITION_NO_PUSH_SOURCES,
            LIVE_DATA_CONDITION_NO_ENFORCE_PULL,
        )
    )
    validate_release_manifest(manifest)
    with pytest.raises(ReleaseError, match="forbidden after a contract migration"):
        assert_manifest_image_rollback_allowed(manifest)

    manifest["migrations"]["core"].update(
        {"revisions": ["core-expand"], "phases": ["expand"]}
    )
    with pytest.raises(ReleaseError, match="only pending revision"):
        validate_release_manifest(manifest)


def test_pending_contract_requires_old_writer_services_to_be_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_hub_platform.operations import release as release_module

    manifest = _manifest(environment="local")
    manifest["migrations"]["raw"].update(
        {
            "previous_head": "20260830_raw_0006",
            "target_head": "20260831_raw_0007",
            "revisions": ["20260831_raw_0007"],
            "phases": ["contract"],
        }
    )
    target = ReleaseTarget(
        compose_file=Path("compose.yaml"),
        env_file=Path(".env"),
        profile="base-access",
    )

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        service = command[-1]
        return SimpleNamespace(
            returncode=0,
            stdout="running-container\n" if service == "platform-api" else "",
        )

    monkeypatch.setattr(release_module, "_run", fake_run)
    with pytest.raises(ReleaseError, match="platform-api"):
        assert_pending_contract_writers_stopped(
            manifest,
            target,
            {"core": "target-core", "raw": "20260830_raw_0006"},
        )


def test_production_manifest_rejects_mutable_images_and_secret_fields() -> None:
    manifest = _manifest()
    manifest["images"]["platform"] = "registry.example.test/ai-hub/platform:latest"
    with pytest.raises(ReleaseError, match="exact tag and registry digest"):
        validate_release_manifest(manifest)

    manifest = _manifest()
    manifest["approval"]["client_secret"] = "must-never-enter-a-manifest"
    with pytest.raises(ReleaseError, match="forbidden secret field"):
        validate_release_manifest(manifest)


def test_release_manifest_requires_every_runtime_gate_and_clean_production_source() -> None:
    manifest = _manifest()
    manifest["gates"].pop()
    with pytest.raises(ReleaseError, match="credential-rotation-runtime"):
        validate_release_manifest(manifest)

    manifest = _manifest()
    manifest["source"]["dirty"] = True
    with pytest.raises(ReleaseError, match="dirty tree"):
        validate_release_manifest(manifest)


def test_m4_credential_migration_is_expand_only_and_old_schema_compatible() -> None:
    target_heads = migration_heads(PROJECT_ROOT)
    assert target_heads["raw"] == "20260831_raw_0007"
    expand_target_heads = {**target_heads, "raw": "20260830_raw_0006"}
    transitions = validate_migration_transition(
        PROJECT_ROOT,
        {
            "core": "20260813_core_0003",
            "raw": "20260816_raw_0001",
        },
        expand_target_heads,
    )

    assert transitions["core"].revisions == (
        "20260814_core_0004",
        "20260814_core_0005",
        "20260815_core_0006",
        "20260816_core_0007",
        "20260816_core_0008",
        "20260816_core_0009",
        "20260816_core_0010",
        "20260821_core_0011",
        "20260822_core_0012",
        "20260823_core_0013",
        "20260823_core_0014",
        "20260824_core_0015",
        "20260824_core_0016",
        "20260824_core_0017",
        "20260824_core_0018",
        "20260824_core_0019",
        "20260829_core_0020",
        "20260829_core_0021",
        "20260830_core_0022",
        "20260830_core_0023",
        "20260830_core_0024",
    )
    assert transitions["core"].phases == (
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
    )
    assert transitions["core"].rollback_schema_compatible is False
    assert transitions["raw"].revisions == (
        "20260829_raw_0002",
        "20260830_raw_0003",
        "20260830_raw_0004",
        "20260830_raw_0005",
        "20260830_raw_0006",
    )
    assert transitions["raw"].phases == (
        "expand",
        "expand",
        "expand",
        "expand",
        "expand",
    )
    assert transitions["raw"].rollback_schema_compatible is True
    assert set(transitions) == {"core", "raw"}


def test_change_record_purpose_contract_requires_approved_window() -> None:
    target_heads = migration_heads(PROJECT_ROOT)
    previous_heads = {
        "core": target_heads["core"],
        "raw": "20260830_raw_0006",
    }
    with pytest.raises(ReleaseError, match="separate approved window"):
        validate_migration_transition(
            PROJECT_ROOT,
            previous_heads,
            target_heads,
        )

    transitions = validate_migration_transition(
        PROJECT_ROOT,
        previous_heads,
        target_heads,
        allow_contract=True,
    )
    assert transitions["core"].revisions == ()
    assert transitions["raw"].revisions == ("20260831_raw_0007",)
    assert transitions["raw"].phases == ("contract",)
    assert transitions["raw"].rollback_schema_compatible is False

    with pytest.raises(ReleaseError, match="only pending revision"):
        validate_migration_transition(
            PROJECT_ROOT,
            {
                "core": "20260830_core_0023",
                "raw": "20260830_raw_0006",
            },
            target_heads,
            allow_contract=True,
        )


def test_image_rollback_without_schema_compat_requires_no_push_sources() -> None:
    credentials = (LIVE_DATA_CONDITION_CREDENTIALS,)
    combined = live_data_conditions_for_rollback(schema_compatible=False)
    assert format_live_data_condition() == LIVE_DATA_CONDITION_CREDENTIALS
    assert LIVE_DATA_CONDITION_NO_PUSH_SOURCES not in parse_live_data_conditions(
        format_live_data_condition()
    )
    assert LIVE_DATA_CONDITION_NO_PUSH_SOURCES in combined
    assert LIVE_DATA_CONDITION_NO_ENFORCE_PULL in combined
    assert_image_rollback_declared(schema_compatible=True, conditions=credentials)
    assert_image_rollback_declared(schema_compatible=False, conditions=combined)
    with pytest.raises(ReleaseError, match="schema-compatible image rollback"):
        assert_image_rollback_declared(schema_compatible=False, conditions=credentials)
    with pytest.raises(ReleaseError, match="schema-compatible image rollback"):
        assert_image_rollback_declared(
            schema_compatible=False,
            conditions=(*credentials, LIVE_DATA_CONDITION_NO_PUSH_SOURCES),
        )
    with pytest.raises(ReleaseError, match="Unknown rollback live-data condition"):
        parse_live_data_conditions("not_a_real_condition")
    with pytest.raises(ReleaseError, match="must include"):
        parse_live_data_conditions(LIVE_DATA_CONDITION_NO_PUSH_SOURCES)


def test_push_rollback_probe_does_not_parse_ingest_source_on_old_schema() -> None:
    source = (
        PROJECT_ROOT / "backend/src/ai_hub_platform/operations/release.py"
    ).read_text(encoding="utf-8")
    exists_sql = source.split("_PUSH_COLUMN_EXISTS_SQL = ", 1)[1].split(
        "_PUSH_SOURCE_COUNT_SQL", 1
    )[0]
    count_sql = source.split("_PUSH_SOURCE_COUNT_SQL = ", 1)[1].split(
        "def _assert_no_push_ingest_sources", 1
    )[0]
    function = source.split("def _assert_no_push_ingest_sources", 1)[1].split(
        "\ndef assert_live_rollback_compatible", 1
    )[0]
    assert "FROM platform_core.ingest_source" not in exists_sql
    assert "information_schema.columns" in exists_sql
    assert "FROM platform_core.ingest_source" in count_sql
    assert function.index("_PUSH_COLUMN_EXISTS_SQL") < function.index(
        "_PUSH_SOURCE_COUNT_SQL"
    )
    assert "SELECT CASE" not in function
    enforce_sql = source.split("_ENFORCE_PULL_COUNT_SQL = ", 1)[1].split(
        "def _assert_no_push_ingest_sources", 1
    )[0]
    assert "contract_validation_mode = 'ENFORCE'" in enforce_sql
    assert "AND enabled" not in enforce_sql
    assert "_assert_no_enforce_pull_ingest_sources" in function


def test_release_manifest_rejects_omitting_credential_live_data_condition() -> None:
    manifest = _manifest()
    manifest["rollback"]["live_data_condition"] = LIVE_DATA_CONDITION_NO_PUSH_SOURCES
    schema = json.loads((OPERATIONS / "release-manifest.schema.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(  # pyright: ignore[reportUnknownMemberType]
            manifest
        )
    with pytest.raises(ReleaseError, match="must include"):
        validate_release_manifest(manifest)
    manifest = _manifest()
    manifest["rollback"]["live_data_condition"] = format_live_data_condition()
    schema = json.loads((OPERATIONS / "release-manifest.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(  # pyright: ignore[reportUnknownMemberType]
        manifest
    )
    validate_release_manifest(manifest)


def test_expand_migration_gate_rejects_unreviewed_destructive_operation(
    tmp_path: Path,
) -> None:
    for component in ("core", "raw"):
        directory = tmp_path / "backend" / "migrations" / "versions" / component
        directory.mkdir(parents=True)
        (directory / "base.py").write_text(
            "revision = 'base'\ndown_revision = None\ndef upgrade():\n    pass\n",
            encoding="utf-8",
        )
    (tmp_path / "backend/migrations/versions/core/breaking.py").write_text(
        """
from alembic import op
revision = "breaking"
down_revision = "base"
release_phase = "expand"
def upgrade():
    op.drop_table("important_data")
""",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseError, match="destructive operations"):
        validate_migration_transition(
            tmp_path,
            {
                "core": "base",
                "raw": "base",
            },
            {
                "core": "breaking",
                "raw": "base",
            },
        )


def test_verified_backup_receipt_requires_hash_off_host_and_freshness(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, 10, tzinfo=UTC)
    backup_id = "ai-hub-backup-20260814T095000Z-deadbeef"
    archive = tmp_path / f"{backup_id}.tar.aesgcm"
    archive.write_bytes(b"encrypted-production-backup")
    import hashlib

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n",
        encoding="utf-8",
    )
    receipt = archive.with_suffix(archive.suffix + ".verified.json")
    document = {
        "schema_version": 1,
        "verified": True,
        "archive": archive.name,
        "archive_sha256": digest,
        "backup_id": backup_id,
        "created_at": (now - timedelta(minutes=10)).isoformat(),
        "verified_at": (now - timedelta(minutes=5)).isoformat(),
        "storage_class": "off-host",
        "profile": "base-access",
    }
    receipt.write_text(json.dumps(document), encoding="utf-8")

    validated = validate_backup_receipt(receipt, now=now)
    assert validated["backup_id"] == backup_id

    document["storage_class"] = "local-drill"
    receipt.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReleaseError, match="off-host"):
        validate_backup_receipt(receipt, now=now)

    document["storage_class"] = "off-host"
    document["created_at"] = (now - timedelta(minutes=61)).isoformat()
    receipt.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ReleaseError, match="older than"):
        validate_backup_receipt(receipt, now=now)


def test_compose_internal_images_accept_complete_digest_references() -> None:
    compose = (PROJECT_ROOT / "deploy/compose.yaml").read_text(encoding="utf-8")
    environment = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "${AI_HUB_PLATFORM_IMAGE_REF:-ai-hub-platform:local}" in compose
    assert "${AI_HUB_PORTAL_IMAGE_REF:-ai-hub-portal:local}" in compose
    assert "${STANDALONE_APP_IMAGE_REF:-ai-hub-standalone-example:local}" in compose
    assert "AI_HUB_PLATFORM_IMAGE_REF=ai-hub-platform:local" in environment
    assert "AI_HUB_IMAGE_TAG=" not in environment


def test_release_runbook_and_cli_cover_canary_promote_and_safe_rollback() -> None:
    runbook = (PROJECT_ROOT / "docs/runbooks/release-rollback.md").read_text(encoding="utf-8")
    source = (PROJECT_ROOT / "backend/src/ai_hub_platform/operations/release.py").read_text(
        encoding="utf-8"
    )

    for command in ("create-manifest", "preflight", "canary", "promote", "rollback"):
        assert command in runbook
        assert command in source
    assert "base-access` 检查并执行平台核心迁移与 raw 贴源层迁移" in runbook
    assert "20260831_raw_0007" in runbook
    assert "--allow-contract" in runbook
    assert 'create.add_argument("--allow-contract"' in source
    assert "金丝雀命令会自动重新执行完整预检" in runbook
    assert "提升命令会再次执行完整预检和隔离金丝雀" in runbook
    assert "preflight = release_preflight" in source
    assert "canary = _run_verified_canary" in source
    assert "forward fix" in source
    assert "database_downgraded" in source
