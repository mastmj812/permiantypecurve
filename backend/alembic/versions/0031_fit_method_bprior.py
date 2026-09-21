"""Add fit_method enum values for short-history b regularization.

'rate_cum_bprior' / 'rate_time_fallback_bprior' tag default-path fits
whose b was pulled toward the bench prior (app.forecasting.b_prior)
because the well had fewer than 36 post-peak fit months. The audit
payload rides the existing forecasts.diagnostics JSONB — no new column.

Revision ID: 0031_fit_method_bprior
Revises: 0030_ratio_forecast_mode
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031_fit_method_bprior"
down_revision: str | Sequence[str] | None = "0030_ratio_forecast_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction in Postgres.
    # Same autocommit pattern as 0005 / 0011.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fit_method ADD VALUE IF NOT EXISTS 'rate_cum_bprior'")
        op.execute("ALTER TYPE fit_method ADD VALUE IF NOT EXISTS 'rate_time_fallback_bprior'")


def downgrade() -> None:
    # Postgres can't remove enum values. Rows tagged with the *_bprior
    # methods would need to be refit or retagged before a true rollback.
    pass
