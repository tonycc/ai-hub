"""Restrict position deletion while assignments exist.

The ``user_organization_position.position_code`` foreign key used
``ON DELETE CASCADE``, so a concurrent assignment inserted between the
occupancy check and the delete would be silently cascaded away. Recreate the
constraint with ``RESTRICT`` so the database itself enforces the "do not
delete an occupied position" rule; the service layer converts the foreign key
violation into the existing conflict response.

Revision ID: 20260824_core_0018
Revises: 20260824_core_0017
Create Date: 2026-08-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260824_core_0018"
down_revision: str | None = "20260824_core_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

release_phase = "expand"
rollback_compatible_with = {"20260824_core_0017"}
# Swapping CASCADE for RESTRICT tightens validation for new writes; it does
# not rewrite or delete existing rows, so old images stay compatible.
compatibility_exceptions = {
    "drop_constraint:user_organization_position_position_code_fkey",
    "create_foreign_key:user_organization_position_position_code_fkey",
}


def upgrade() -> None:
    op.drop_constraint(
        "user_organization_position_position_code_fkey",
        "user_organization_position",
        schema="platform_core",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "user_organization_position_position_code_fkey",
        "user_organization_position",
        "position_definition",
        ["position_code"],
        ["position_code"],
        source_schema="platform_core",
        referent_schema="platform_core",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "user_organization_position_position_code_fkey",
        "user_organization_position",
        schema="platform_core",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "user_organization_position_position_code_fkey",
        "user_organization_position",
        "position_definition",
        ["position_code"],
        ["position_code"],
        source_schema="platform_core",
        referent_schema="platform_core",
        ondelete="CASCADE",
    )
