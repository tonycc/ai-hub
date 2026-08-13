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
    assert "('SECURITY_AUDITOR', 'platform.notification.read')" in sql
    assert "platform_projection" not in sql
    assert "integration_outbox" not in sql
    assert "integration_inbox" not in sql
    assert "event_contract_registration" not in sql


def test_platform_projection_migration_does_not_create_core_objects() -> None:
    sql = render_upgrade_sql("alembic-projection.ini")

    assert "CREATE TABLE platform_projection.alembic_version" in sql
    assert "CREATE TABLE platform_projection.integration_inbox" in sql
    assert "CREATE TABLE platform_projection.projection_checkpoint" in sql
    assert "CREATE TABLE platform_projection.example_record_projection" in sql
    assert "CREATE TABLE platform_projection.projection_gap" in sql
    assert "platform_core" not in sql
    assert "integration_outbox" not in sql


def test_event_registration_migration_enables_only_registered_contracts() -> None:
    sql = render_upgrade_sql("alembic-events.ini")

    assert "CREATE TABLE platform_core.alembic_version_events" in sql
    assert "CREATE TABLE platform_core.event_contract_registration" in sql
    assert "EVENT_PUBLISHER" in sql
    assert "PROJECTION_SOURCE" in sql
    assert "company.example.record.changed.v1" in sql
    assert "company.example.record.deleted.v1" in sql
    assert "CREATE TABLE platform_core.application" not in sql
    assert "platform_projection" not in sql
