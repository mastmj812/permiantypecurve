"""Add forecasts.qo + forecasts.peak_index_months for the linear-ramp prefix.

Every per-well forecast now models ramp(qo→qi over peak_index_months)
+ Arps instead of pure Arps anchored at peak. The two new columns
carry the well's first-prod-month rate per stream (qo) and the months
from first prod to peak (peak_index_months). Both NULL on rows
existing before this migration; the backfill UPDATE populates them
from production_monthly + wells where possible.

Rows whose qo/peak_index_months stay NULL after backfill (no first-
prod observation, peak before first prod, etc.) read as pure Arps via
the evaluator's fallback — same behavior as before this migration.

Revision ID: 0013_forecast_ramp_params
Revises: 0012_wells_novi_oil_eur
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_forecast_ramp_params"
down_revision: str | Sequence[str] | None = "0012_wells_novi_oil_eur"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Per-stream backfill. The rate column in production_monthly varies by
# stream; the lateral join picks the first non-null observation per
# api10 to anchor qo. peak_index_months is the calendar month gap from
# first_prod_date to peak_month_date, clamped to >= 0.
_BACKFILL_SQL = """
WITH first_obs AS (
    SELECT DISTINCT ON (api10)
        api10,
        rate_calday_bopd,
        rate_calday_mcfd,
        rate_calday_bwpd
    FROM production_monthly
    WHERE rate_calday_bopd IS NOT NULL
       OR rate_calday_mcfd IS NOT NULL
       OR rate_calday_bwpd IS NOT NULL
    ORDER BY api10, prod_date ASC
)
UPDATE forecasts f
SET
    qo = CASE f.stream::text
        WHEN 'oil'   THEN fo.rate_calday_bopd
        WHEN 'gas'   THEN fo.rate_calday_mcfd
        WHEN 'water' THEN fo.rate_calday_bwpd
    END,
    peak_index_months = GREATEST(
        0,
        ((EXTRACT(YEAR FROM f.peak_month_date)::int - EXTRACT(YEAR FROM w.first_prod_date)::int) * 12
         + (EXTRACT(MONTH FROM f.peak_month_date)::int - EXTRACT(MONTH FROM w.first_prod_date)::int))
    )
FROM wells w, first_obs fo
WHERE w.api10 = f.api10
  AND fo.api10 = f.api10
  AND f.peak_month_date IS NOT NULL
  AND w.first_prod_date IS NOT NULL;
"""


def upgrade() -> None:
    op.add_column(
        "forecasts",
        sa.Column("qo", sa.Float(), nullable=True),
    )
    op.add_column(
        "forecasts",
        sa.Column("peak_index_months", sa.Integer(), nullable=True),
    )
    # Best-effort backfill so existing wells render the ramp segment in
    # the chart without a re-forecast. Wells missing first_prod_date or
    # the matching first-month production row stay NULL — evaluator
    # falls back to pure Arps for those.
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("forecasts", "peak_index_months")
    op.drop_column("forecasts", "qo")
