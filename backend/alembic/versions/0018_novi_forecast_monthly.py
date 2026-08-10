"""Add novi_forecast_monthly.

Local mirror of Novi's forecasted PDP series (curated.production_forecast),
synced alongside actual production. Surfaced as a benchmark overlay in the
ForecastDetailModal. Plain table (no Timescale hypertable) — the forecast
tail is small relative to actuals. Populated on the next sync; empty until
then.

Revision ID: 0018_novi_forecast_monthly
Revises: 0017_deal_polygons
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_novi_forecast_monthly"
down_revision: str | Sequence[str] | None = "0017_deal_polygons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "novi_forecast_monthly",
        sa.Column("api10", sa.String(length=10), nullable=False),
        sa.Column("prod_date", sa.Date(), nullable=False),
        sa.Column("rate_calday_bopd", sa.Float(), nullable=True),
        sa.Column("rate_calday_mcfd", sa.Float(), nullable=True),
        sa.Column("rate_calday_bwpd", sa.Float(), nullable=True),
        sa.Column("cumulative_oil_bbl", sa.Float(), nullable=True),
        sa.Column("cumulative_gas_mcf", sa.Float(), nullable=True),
        sa.Column("cumulative_water_bbl", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["api10"], ["wells.api10"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("api10", "prod_date"),
    )


def downgrade() -> None:
    op.drop_table("novi_forecast_monthly")
