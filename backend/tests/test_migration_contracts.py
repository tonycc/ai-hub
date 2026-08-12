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
    assert "REVOKE ALL ON platform_core.alembic_version FROM ai_hub_platform" in sql
    assert "platform_projection" not in sql
    assert "integration_outbox" not in sql
    assert "integration_inbox" not in sql


def test_platform_projection_migration_does_not_create_core_objects() -> None:
    sql = render_upgrade_sql("alembic-projection.ini")

    assert "CREATE TABLE platform_projection.alembic_version" in sql
    assert "CREATE TABLE platform_projection.integration_inbox" in sql
    assert "platform_core" not in sql
    assert "integration_outbox" not in sql
