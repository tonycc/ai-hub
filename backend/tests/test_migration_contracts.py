from __future__ import annotations

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def render_upgrade_sql(config_name: str) -> str:
    output = StringIO()
    config = Config(BACKEND_ROOT / config_name, output_buffer=output)

    command.upgrade(config, "head", sql=True)

    return output.getvalue()


def test_platform_core_migration_establishes_m1_core_and_protected_audit() -> None:
    sql = render_upgrade_sql("alembic.ini")

    assert "CREATE TABLE platform_core.alembic_version" in sql
    assert "CREATE TABLE platform_core.application" in sql
    assert "CREATE TABLE platform_core.application_environment" in sql
    assert "CREATE TABLE platform_core.identity_user" in sql
    assert "CREATE TABLE platform_core.organization" in sql
    assert "CREATE TABLE platform_core.permission_definition" in sql
    assert "CREATE TABLE platform_core.permission_grant" in sql
    assert "CREATE TABLE platform_core.notification" in sql
    assert "CREATE TABLE platform_core.audit_event" in sql
    assert "REVOKE SELECT, UPDATE, DELETE ON platform_core.audit_event" in sql
    assert "CREATE TABLE platform_core.platform_role_definition" in sql
    assert "CREATE TABLE platform_core.authorization_role" in sql
    assert "CREATE TABLE platform_core.application_credential" in sql
    assert "service_subject VARCHAR(255) NOT NULL" in sql
    assert "UNIQUE (service_subject)" in sql
    assert "CREATE TABLE platform_core.portal_session" in sql
    assert "CREATE TABLE platform_core.conformance_run" in sql
    assert "GRANT SELECT ON platform_core.audit_event TO ai_hub_platform" in sql
    assert "REVOKE UPDATE, DELETE ON platform_core.audit_event" in sql
    assert "REVOKE ALL ON platform_core.alembic_version FROM ai_hub_platform" in sql
    assert "('PLATFORM_ADMIN', 'platform.notification.read')" in sql
    assert "('APPLICATION_DEVELOPER', 'platform.notification.read')" in sql
    # INSERT statements should not contain retired roles; 0011 only disables
    assert "INSERT INTO platform_core.platform_role_permission" in sql
    assert "UPDATE platform_core.platform_role_definition" in sql
    assert "DELETE FROM platform_core.platform_role_definition" not in sql
    assert "DELETE FROM platform_core.platform_role_permission" not in sql
    assert "platform_projection" not in sql
    assert "platform_raw" not in sql
    assert "integration_outbox" not in sql
    assert "integration_inbox" not in sql
    assert "event_contract_registration" not in sql
    assert "ck_conformance_check_profile" in sql
    assert "profile IN ('API_ONLY', 'DATA_INGEST')" in sql
    assert "profile IN ('DATA_INGEST')" in sql
    assert "CREATE TABLE platform_core.ingest_contract" in sql
    assert "CREATE TABLE platform_core.ingest_contract_certification" in sql
    assert "GRANT SELECT ON platform_core.ingest_contract TO ai_hub_raw" in sql
    assert "ai_hub.ingest.push" in sql
    assert "ck_ingest_source_transport_fields" in sql
    assert "20260829_core_0020" in sql
    assert "20260829_core_0021" in sql
    assert "20260830_core_0022" in sql
    assert "20260830_core_0023" in sql
    assert "20260830_core_0024" in sql
    assert "20260901_core_0025" in sql
    assert "20260902_core_0026" in sql
    assert "20260902_core_0027" in sql
    assert "20260905_core_0028" in sql
    assert "CREATE TABLE platform_core.application_admin_bootstrap" in sql
    assert "platform.application.bootstrap" in sql
    assert "platform.directory.read" in sql
    assert "profile IN ('OIDC_ONLY', 'API_ONLY', 'DATA_INGEST')" in sql
    assert "PLATFORM_INGEST_OPERATOR" in sql
    assert "platform.ingest.certify.data_owner" in sql
    assert "platform.ingest.certify.operator" in sql
    assert "push_staging_retention_hours" in sql
    core_0020 = (
        BACKEND_ROOT / "migrations/versions/core/20260829_core_0020.py"
    ).read_text()
    assert "DELETE FROM platform_core.ingest_source" in core_0020
    assert "transport_mode = 'PUSH_AGENT'" in core_0020
    assert "rollback_compatible_with =" not in core_0020
    core_0021 = (
        BACKEND_ROOT / "migrations/versions/core/20260829_core_0021.py"
    ).read_text()
    assert "push_staging_retention_hours" in core_0021
    assert 'rollback_compatible_with = {"20260829_core_0020"}' in core_0021
    core_0022 = (
        BACKEND_ROOT / "migrations/versions/core/20260830_core_0022.py"
    ).read_text()
    assert "full_regression_evidence_ref" in core_0022
    assert 'rollback_compatible_with = {"20260829_core_0021"}' in core_0022
    core_0023 = (
        BACKEND_ROOT / "migrations/versions/core/20260830_core_0023.py"
    ).read_text()
    assert "PLATFORM_INGEST_OPERATOR" in core_0023
    assert "platform.ingest.certify.operator" in core_0023
    assert "ai-hub-platform-ingest-operator" in core_0023
    assert 'rollback_compatible_with = {"20260830_core_0022"}' in core_0023
    core_0024 = (
        BACKEND_ROOT / "migrations/versions/core/20260830_core_0024.py"
    ).read_text()
    assert "transport_mode" in core_0024
    assert "ck_ingest_contract_certification_transport_mode" in core_0024
    assert 'rollback_compatible_with = {"20260830_core_0023"}' in core_0024
    assert "FROM platform_core.ingest_source" not in core_0024
    core_0025 = (
        BACKEND_ROOT / "migrations/versions/core/20260901_core_0025.py"
    ).read_text()
    assert 'rollback_compatible_with = {"20260830_core_0024"}' in core_0025
    assert "application_admin_bootstrap" in core_0025
    assert "ON CONFLICT (application_id, environment) DO NOTHING" in core_0025
    assert "organization_touch_directory_users" in core_0025
    core_0026 = (
        BACKEND_ROOT / "migrations/versions/core/20260902_core_0026.py"
    ).read_text()
    assert 'down_revision: str | None = "20260901_core_0025"' in core_0026
    assert "initial_admin_user_id" in core_0026
    assert "created_by_user_id" in core_0026
    core_0027 = (
        BACKEND_ROOT / "migrations/versions/core/20260902_core_0027.py"
    ).read_text()
    assert 'down_revision: str | None = "20260902_core_0026"' in core_0027
    assert "identity_directory_revision_state" in core_0027
    assert "assign_identity_directory_revision" in core_0027
    core_0028 = (
        BACKEND_ROOT / "migrations/versions/core/20260905_core_0028.py"
    ).read_text()
    assert 'down_revision: str | None = "20260902_core_0027"' in core_0028
    assert 'release_phase = "expand"' in core_0028
    assert 'rollback_compatible_with = {"20260902_core_0027"}' in core_0028
    assert "portal_origin" in core_0028
    assert "redirect_uri" in core_0028


def test_platform_raw_migration_establishes_ingest_tables_without_core_objects() -> None:
    sql = render_upgrade_sql("alembic-raw.ini")

    assert "CREATE TABLE platform_raw.alembic_version" in sql
    assert "CREATE TABLE platform_raw.raw_sync_cursor" in sql
    assert "CREATE TABLE platform_raw.raw_ingest_batch" in sql
    assert "CREATE TABLE platform_raw.raw_change_record" in sql
    assert "CREATE TABLE platform_raw.raw_current_state" in sql
    assert "uq_raw_change_record_idempotent" in sql
    assert "payload_contract_version" in sql
    assert "platform_core" not in sql
    assert "platform_projection" not in sql
    assert "integration_outbox" not in sql
    assert "CREATE TABLE platform_raw.raw_push_generation" in sql
    assert "CREATE TABLE platform_raw.raw_push_staging" in sql
    assert "CREATE TABLE platform_raw.raw_push_batch_receipt" in sql
    assert "CREATE TABLE platform_raw.raw_push_committed_watermark" in sql
    assert "CREATE TABLE platform_raw.raw_push_generation_transition" in sql
    assert "ix_raw_push_generation_client_lease" in sql
    assert "ix_raw_push_generation_worker_lease" in sql
    assert "20260830_raw_0003" in sql
    assert "20260830_raw_0004" in sql
    assert "20260830_raw_0005" in sql
    assert "20260830_raw_0006" in sql
    assert "20260831_raw_0007" in sql
    raw_0006 = (BACKEND_ROOT / "migrations/versions/raw/20260830_raw_0006.py").read_text()
    upgrade = raw_0006.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "uq_raw_change_record_idempotent" not in upgrade
    assert "purpose" in sql
    assert "audit_summary" in sql
    assert "request_id" in sql
    assert "completion_request" in sql
    assert "uq_raw_push_generation_one_active" in sql
    assert "uq_raw_ingest_batch_external_id" in sql
    assert "uq_raw_change_record_idempotent" in sql
    assert "uq_raw_change_record_idempotent_purpose" in sql
    assert "transport_mode" in sql
    raw_0007 = (BACKEND_ROOT / "migrations/versions/raw/20260831_raw_0007.py").read_text()
    assert 'release_phase = "contract"' in raw_0007
    assert "rollback_compatible_with" not in raw_0007
    contract_upgrade = raw_0007.split("def upgrade", 1)[1].split(
        "def downgrade", 1
    )[0]
    assert "PURPOSE_CONSTRAINT" in contract_upgrade
    assert "LEGACY_CONSTRAINT" in contract_upgrade


def test_certification_transport_mode_is_not_backfilled_from_source() -> None:
    core_0024 = (
        BACKEND_ROOT / "migrations/versions/core/20260830_core_0024.py"
    ).read_text()
    assert "FROM platform_core.ingest_source" not in core_0024
    assert 'server_default="PULL_EXPORT"' in core_0024


def test_retired_event_and_projection_migration_configs_are_gone() -> None:
    assert not (BACKEND_ROOT / "alembic-events.ini").exists()
    assert not (BACKEND_ROOT / "alembic-projection.ini").exists()
