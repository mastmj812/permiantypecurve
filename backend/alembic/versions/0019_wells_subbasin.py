"""Add wells.subbasin.

Permian sub-basin (Delaware / Midland / Central Basin Platform / …) from
curated.wells_enriched.subbasin. Drives the basin-aware terminal-Df in
forecasting (Midland = 0.06, Delaware + others = 0.08). Populated on the
next well sync (and by a one-time backfill); existing rows land at NULL
until then, which the forecaster treats as the default 0.08.

Revision ID: 0019_wells_subbasin
Revises: 0018_novi_forecast_monthly
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_wells_subbasin"
down_revision: str | Sequence[str] | None = "0018_novi_forecast_monthly"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wells", sa.Column("subbasin", sa.String(length=40), nullable=True))
    op.create_index("ix_wells_subbasin", "wells", ["subbasin"])


def downgrade() -> None:
    op.drop_index("ix_wells_subbasin", table_name="wells")
    op.drop_column("wells", "subbasin")
