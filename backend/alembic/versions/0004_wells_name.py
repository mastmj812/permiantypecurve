"""Add wells.name (well/lease name from Enverus).

Revision ID: 0004_wells_name
Revises: 0003_producing_days_float
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_wells_name"
down_revision: str | Sequence[str] | None = "0003_producing_days_float"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wells",
        sa.Column("name", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_wells_name", "wells", ["name"])


def downgrade() -> None:
    op.drop_index("ix_wells_name", table_name="wells")
    op.drop_column("wells", "name")
