"""Add fit_method enum value 'cohort_transfer' + forecasts.diagnostics.

Cohort-transfer is the new fit method for short-history wells in a
batch — their Di and b are pulled from the median of the long-history
wells in the same batch rather than from an unconstrained fit that would
otherwise land on a low-Di / high-EUR solution. `diagnostics` JSONB
holds the donor cohort identifiers and median so each transfer is
auditable.

Revision ID: 0011_cohort_transfer
Revises: 0010_api14_to_api10
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0011_cohort_transfer"
down_revision: str | Sequence[str] | None = "0010_api14_to_api10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction in Postgres.
    # Alembic wraps migrations in an implicit transaction; the autocommit
    # block escapes it for this single statement. Same pattern as 0005.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fit_method ADD VALUE IF NOT EXISTS 'cohort_transfer'")
    op.add_column(
        "forecasts",
        sa.Column("diagnostics", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecasts", "diagnostics")
    # Postgres doesn't support removing enum values. Down-migration leaves
    # the value in place; any rows tagged 'cohort_transfer' would need to
    # be rewritten or deleted first if you intend to truly roll back.
