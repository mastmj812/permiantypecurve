"""producing_days: int → double precision (Enverus reports fractions)

Revision ID: 0003_producing_days_float
Revises: 0002_alignment_first_prod_month
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_producing_days_float"
down_revision: str | Sequence[str] | None = "0002_alignment_first_prod_month"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Real Enverus data reports producing_days as a fractional float
    # (e.g. 0.166 = ~4 hours online in the first month). Truncating to
    # int corrupts the month-1 calday rate exception. Widening to FLOAT
    # is a no-op for existing integer values.
    op.alter_column(
        "production_monthly",
        "producing_days",
        type_=sa.Float(),
        existing_type=sa.Integer(),
        postgresql_using="producing_days::double precision",
    )


def downgrade() -> None:
    # Round on the way back to int. Loses sub-day precision.
    op.alter_column(
        "production_monthly",
        "producing_days",
        type_=sa.Integer(),
        existing_type=sa.Float(),
        postgresql_using="round(producing_days)::integer",
    )
