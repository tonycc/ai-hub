"""Enable the registered M2 event and projection-source capabilities.

Revision ID: 20260812_events_0001
Revises:
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_events_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_contract_registration",
        sa.Column("producer_application_id", sa.String(length=63), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(length=100), nullable=False),
        sa.Column("routing_key", sa.String(length=200), nullable=False),
        sa.Column("data_schema", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "event_version >= 1", name="ck_event_contract_registration_version"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DEPRECATED', 'REVOKED')",
            name="ck_event_contract_registration_status",
        ),
        sa.ForeignKeyConstraint(
            ["producer_application_id"],
            ["platform_core.application.application_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("producer_application_id", "event_type"),
        schema="platform_core",
    )
    op.execute(
        """
        UPDATE platform_core.application
        SET capabilities = ARRAY['API_CLIENT', 'EVENT_PUBLISHER', 'PROJECTION_SOURCE'],
            updated_at = CURRENT_TIMESTAMP
        WHERE application_id = 'standalone-example'
        """
    )
    op.execute(
        """
        INSERT INTO platform_core.event_contract_registration
            (producer_application_id, event_type, event_version, object_type,
             routing_key, data_schema, status)
        VALUES
            ('standalone-example', 'company.example.record.changed.v1', 1,
             'example_record', 'company.example.record.changed.v1',
             'https://ai-hub.example.internal/contracts/events/example-record-event-data.v1.schema.json',
             'ACTIVE'),
            ('standalone-example', 'company.example.record.deleted.v1', 1,
             'example_record', 'company.example.record.deleted.v1',
             'https://ai-hub.example.internal/contracts/events/example-record-event-data.v1.schema.json',
             'ACTIVE')
        ON CONFLICT (producer_application_id, event_type) DO UPDATE
        SET event_version = EXCLUDED.event_version,
            object_type = EXCLUDED.object_type,
            routing_key = EXCLUDED.routing_key,
            data_schema = EXCLUDED.data_schema,
            status = EXCLUDED.status
        """
    )
    op.execute(
        "REVOKE ALL ON platform_core.alembic_version_events FROM ai_hub_platform"
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM platform_core.event_contract_registration
        WHERE producer_application_id = 'standalone-example'
        """
    )
    op.drop_table("event_contract_registration", schema="platform_core")
    op.execute(
        """
        UPDATE platform_core.application
        SET capabilities = ARRAY['API_CLIENT'], updated_at = CURRENT_TIMESTAMP
        WHERE application_id = 'standalone-example'
        """
    )
