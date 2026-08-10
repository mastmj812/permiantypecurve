"""Add wells.novi_oil_eur.

Persists Novi's 50-yr oil EUR (curated.wells_enriched.eur_50yr_oil_bbl)
so the Review table can render it as a benchmark against the app's
autoforecast EUR. Populated on the next well sync — existing rows
land at NULL until then.

Revision ID: 0012_wells_novi_oil_eur
Revises: 0011_cohort_transfer
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_wells_novi_oil_eur"
down_revision: str | Sequence[str] | None = "0011_cohort_transfer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wells",
        sa.Column("novi_oil_eur", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wells", "novi_oil_eur")
