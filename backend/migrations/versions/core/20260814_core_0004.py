"""Add an overlap window for zero-interruption application credential rotation.

Revision ID: 20260814_core_0004
Revises: 20260813_core_0003
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260814_core_0004"
down_revision: str | None = "20260813_core_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Machine-readable release tooling only permits explicitly expand-compatible
# exceptions. Dropping these constraints loosens the old model; it does not
# remove data or invalidate reads/writes from the previous application version.
release_phase = "expand"
compatibility_exceptions = {
    "drop_constraint:uq_application_credential_environment",
    "drop_constraint:ck_application_credential_status",
    "create_check_constraint:ck_application_credential_status",
    "create_unique_index:uq_application_credential_active_environment",
}
# The preceding application version can still read and write the expanded
# schema as long as no environment has entered multi-credential state. Release
# preflight and rollback commands enforce that live-data condition.
rollback_compatible_with = {"20260813_core_0003"}


def upgrade() -> None:
    op.add_column(
        "application_credential",
        sa.Column("revoke_after", sa.DateTime(timezone=True), nullable=True),
        schema="platform_core",
    )
    op.drop_constraint(
        "uq_application_credential_environment",
        "application_credential",
        schema="platform_core",
        type_="unique",
    )
    op.drop_constraint(
        "ck_application_credential_status",
        "application_credential",
        schema="platform_core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_application_credential_status",
        "application_credential",
        "status IN ('ACTIVE', 'DRAINING', 'REVOKED', 'ERROR')",
        schema="platform_core",
    )
    op.create_index(
        "uq_application_credential_active_environment",
        "application_credential",
        ["application_id", "environment"],
        unique=True,
        schema="platform_core",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_application_credential_environment_version",
        "application_credential",
        ["application_id", "environment", sa.text("version DESC")],
        schema="platform_core",
    )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            """
            SELECT application_id, environment
            FROM platform_core.application_credential
            GROUP BY application_id, environment
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade credential overlap while an environment has multiple versions"
        )
    op.drop_index(
        "ix_application_credential_environment_version",
        table_name="application_credential",
        schema="platform_core",
    )
    op.drop_index(
        "uq_application_credential_active_environment",
        table_name="application_credential",
        schema="platform_core",
    )
    op.drop_constraint(
        "ck_application_credential_status",
        "application_credential",
        schema="platform_core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_application_credential_status",
        "application_credential",
        "status IN ('ACTIVE', 'REVOKED', 'ERROR')",
        schema="platform_core",
    )
    op.create_unique_constraint(
        "uq_application_credential_environment",
        "application_credential",
        ["application_id", "environment"],
        schema="platform_core",
    )
    op.drop_column(
        "application_credential",
        "revoke_after",
        schema="platform_core",
    )
