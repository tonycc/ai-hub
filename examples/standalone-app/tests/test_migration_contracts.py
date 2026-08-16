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
    assert "ADD COLUMN aggregate_version" in sql
    assert "CREATE TABLE app.ingest_change_log" in sql
    assert "CREATE TABLE app.ingest_version_counter" in sql
    assert "M1 ownership denial record" in sql
    assert "another-user" in sql
    assert "integration_outbox" not in sql
    assert "integration_inbox" not in sql
    assert "CREATE TABLE alembic_version" in sql
