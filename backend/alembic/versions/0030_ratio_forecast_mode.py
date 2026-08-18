"""Add ratio-mode enum values: model_type 'ratio', fit_method 'ratio_cum_oil'.

Ratio-driven forecasting (gas/water only, engineer-selected): the stream
is fitted as a ratio of cumulative oil and forecast as ratio x the oil
forecast. Such rows carry model_type='ratio' + fit_method='ratio_cum_oil'
with the ratio params (mode/alpha/beta/sub_mode/...) in the params JSONB;
the Arps scalar columns are NULL. No table changes — enum values only.

Postgres cannot drop an enum value, so downgrade re-fits nothing: any
ratio rows are deleted (they are meaningless to a rolled-back app and
were engineer-created; the well can be re-forecast as Arps) and the
enum values remain in place, harmless.

Revision ID: 0030_ratio_forecast_mode
Revises: 0029_well_water_provenance
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030_ratio_forecast_mode"
down_revision: str | Sequence[str] | None = "0029_well_water_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE model_type ADD VALUE IF NOT EXISTS 'ratio'")
    op.execute("ALTER TYPE fit_method ADD VALUE IF NOT EXISTS 'ratio_cum_oil'")


def downgrade() -> None:
    op.execute("DELETE FROM forecasts WHERE model_type = 'ratio'")
