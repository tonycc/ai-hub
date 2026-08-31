"""Contract change-record idempotency by purpose.

The expand revision added and backfilled ``raw_change_record.purpose`` while
retaining the legacy four-column uniqueness key for rolling compatibility.
This contract revision must run in a separately approved window after every
old Pull writer has been stopped: old images still target the four-column
constraint explicitly and cannot write after it is removed.

Downgrade is data preserving but intentionally conditional. Once production
and certification contain the same object/version, the legacy key cannot be
restored without deleting data, so downgrade fails with an explicit error.

Revision ID: 20260831_raw_0007
Revises: 20260830_raw_0006
Create Date: 2026-08-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260831_raw_0007"
down_revision: str | None = "20260830_raw_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "contract"

SCHEMA = "platform_raw"
LEGACY_CONSTRAINT = "uq_raw_change_record_idempotent"
PURPOSE_CONSTRAINT = "uq_raw_change_record_idempotent_purpose"
IDEMPOTENCY_COLUMNS = [
    "source_application_id",
    "object_type",
    "object_id",
    "version",
]


def upgrade() -> None:
    op.create_unique_constraint(
        PURPOSE_CONSTRAINT,
        "raw_change_record",
        [*IDEMPOTENCY_COLUMNS, "purpose"],
        schema=SCHEMA,
    )
    op.drop_constraint(
        LEGACY_CONSTRAINT,
        "raw_change_record",
        schema=SCHEMA,
        type_="unique",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM platform_raw.raw_change_record
                GROUP BY source_application_id, object_type, object_id, version
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'cannot restore four-column change-record '
                        || 'uniqueness: cross-purpose object versions exist';
            END IF;
        END
        $$
        """
    )
    op.create_unique_constraint(
        LEGACY_CONSTRAINT,
        "raw_change_record",
        IDEMPOTENCY_COLUMNS,
        schema=SCHEMA,
    )
    op.drop_constraint(
        PURPOSE_CONSTRAINT,
        "raw_change_record",
        schema=SCHEMA,
        type_="unique",
    )
