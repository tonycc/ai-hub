"""Add a transactional source watermark to the optional Outbox.

Revision ID: 20260812_publisher_0002
Revises: 20260812_publisher_0001
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_publisher_0002"
down_revision: str | None = "20260812_publisher_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_source_state",
        sa.Column("application_id", sa.String(length=63), nullable=False),
        sa.Column("current_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "current_sequence >= 0", name="ck_integration_source_state_sequence"
        ),
        sa.PrimaryKeyConstraint("application_id"),
        schema="app",
    )
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON app.integration_source_state "
        "FROM standalone_app"
    )
    op.execute(
        "GRANT UPDATE (current_sequence, updated_at) "
        "ON app.integration_source_state TO standalone_app"
    )
    op.execute(
        """
        INSERT INTO app.integration_source_state (application_id, current_sequence)
        VALUES ('standalone-example', 0)
        ON CONFLICT (application_id) DO NOTHING
        """
    )
    op.add_column(
        "integration_outbox",
        sa.Column("source_sequence", sa.BigInteger(), nullable=True),
        schema="app",
    )
    op.execute(
        """
        UPDATE app.integration_outbox
        SET source_sequence = numbered.source_sequence
        FROM (
            SELECT event_id, row_number() OVER (ORDER BY created_at, event_id) AS source_sequence
            FROM app.integration_outbox
        ) AS numbered
        WHERE app.integration_outbox.event_id = numbered.event_id
        """
    )
    op.alter_column(
        "integration_outbox",
        "source_sequence",
        schema="app",
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_app_outbox_source_sequence",
        "integration_outbox",
        ["source_sequence"],
        schema="app",
    )
    op.execute(
        """
        WITH initial_records AS (
            SELECT record.id, record.name, record.state, record.owner_subject,
                   record.aggregate_version, record.updated_at,
                   COALESCE(
                       (SELECT max(source_sequence) FROM app.integration_outbox), 0
                   ) + row_number() OVER (ORDER BY record.id) AS source_sequence
            FROM app.example_record AS record
            WHERE NOT EXISTS (
                SELECT 1 FROM app.integration_outbox AS existing
                WHERE existing.subject = 'example-record/' || record.id::text
            )
        )
        INSERT INTO app.integration_outbox
            (event_id, event_type, source, subject, occurred_at, payload,
             headers, status, attempts, next_attempt_at, source_sequence)
        SELECT
            md5('ai-hub-m2-initial:' || id::text)::uuid,
            'company.example.record.changed.v1',
            'urn:ai-hub:application:standalone-example',
            'example-record/' || id::text,
            updated_at,
            jsonb_build_object(
                'specversion', '1.0',
                'id', md5('ai-hub-m2-initial:' || id::text)::uuid,
                'source', 'urn:ai-hub:application:standalone-example',
                'type', 'company.example.record.changed.v1',
                'subject', 'example-record/' || id::text,
                'time', updated_at,
                'datacontenttype', 'application/json',
                'dataschema', 'https://ai-hub.example.internal/contracts/events/example-record-event-data.v1.schema.json',
                'producer_application_id', 'standalone-example',
                'event_version', 1,
                'aggregate_version', aggregate_version,
                'source_sequence', source_sequence,
                'object_type', 'example_record',
                'trace_id', NULL,
                'actor', jsonb_build_object('type', 'system', 'id', 'm2-bootstrap'),
                'data_classification', 'internal',
                'data', jsonb_build_object(
                    'record_id', id,
                    'name', name,
                    'state', state,
                    'owner_subject', owner_subject
                )
            ),
            jsonb_build_object(
                'content_type', 'application/cloudevents+json',
                'schema_version', 1
            ),
            'PENDING', 0, CURRENT_TIMESTAMP, source_sequence
        FROM initial_records
        """
    )
    op.execute(
        """
        UPDATE app.integration_source_state
        SET current_sequence = COALESCE(
            (SELECT max(source_sequence) FROM app.integration_outbox), 0
        ), updated_at = CURRENT_TIMESTAMP
        WHERE application_id = 'standalone-example'
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_app_outbox_source_sequence",
        "integration_outbox",
        schema="app",
        type_="unique",
    )
    op.drop_column("integration_outbox", "source_sequence", schema="app")
    op.drop_table("integration_source_state", schema="app")
