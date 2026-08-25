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
    assert "20260816_core_0009" in sql


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
    assert "REVOKE ALL ON platform_raw.alembic_version FROM ai_hub_platform, ai_hub_raw" in sql


def test_retired_event_and_projection_migration_configs_are_gone() -> None:
    assert not (BACKEND_ROOT / "alembic-events.ini").exists()
    assert not (BACKEND_ROOT / "alembic-projection.ini").exists()
