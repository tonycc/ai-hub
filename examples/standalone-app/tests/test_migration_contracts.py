from __future__ import annotations

from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

APP_ROOT = Path(__file__).resolve().parents[1]


def render_upgrade_sql(config_name: str) -> str:
    output = StringIO()
    config = Config(APP_ROOT / config_name, output_buffer=output)

    command.upgrade(config, "head", sql=True)

    return output.getvalue()


def test_api_client_migration_creates_reference_record_with_m1_owner() -> None:
    sql = render_upgrade_sql("alembic.ini")

    assert "CREATE TABLE app.example_record" in sql
    assert "ADD COLUMN owner_subject" in sql
    assert "M1 ownership denial record" in sql
    assert "another-user" in sql
    assert "integration_outbox" not in sql
    assert "integration_inbox" not in sql
    assert "CREATE TABLE alembic_version" in sql


def test_event_publisher_migration_only_creates_outbox() -> None:
    sql = render_upgrade_sql("alembic-event-publisher.ini")

    assert "CREATE TABLE app.integration_outbox" in sql
    assert "ix_app_outbox_dispatch" in sql
    assert "example_record" not in sql
    assert "integration_inbox" not in sql
    assert "alembic_version_event_publisher" in sql


def test_event_consumer_migration_only_creates_inbox() -> None:
    sql = render_upgrade_sql("alembic-event-consumer.ini")

    assert "CREATE TABLE app.integration_inbox" in sql
    assert "example_record" not in sql
    assert "integration_outbox" not in sql
    assert "alembic_version_event_consumer" in sql
