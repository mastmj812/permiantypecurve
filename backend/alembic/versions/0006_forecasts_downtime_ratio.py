"""Add forecasts.downtime_ratio.

Persists the fraction of post-peak months that were flagged as downtime
(rate << local trend) and excluded from the fit. The Review grid uses
this to flag wells whose forecast was built on a noisy production
profile so the engineer knows to eyeball them.

Revision ID: 0006_forecasts_downtime_ratio
Revises: 0005_fit_method_fallback
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_forecasts_downtime_ratio"
down_revision: str | Sequence[str] | None = "0005_fit_method_fallback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "forecasts",
        sa.Column("downtime_ratio", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecasts", "downtime_ratio")
