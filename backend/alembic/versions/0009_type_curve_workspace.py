"""Type Curve workspace: is_stale flag + forecast_overrides JSONB.

Phase 1 of the workspace conversion: make a saved Type Curve navigable
into its source forecasts. Phase 1 only READS these new columns; Phase 2
sets them when a TC's underlying forecasts or membership changes.

What this adds:
  * ``type_curves.is_stale`` — bool flag set by Phase 2 when an
    override / global forecast / membership change since the last
    aggregation invalidates the cached ``series``. Defaults false; no
    backfill (existing curves are considered fresh).
  * ``type_curves.forecast_overrides`` — JSONB keyed by
    ``{api14: {stream: <forecast payload>}}`` carrying per-TC forecast
    overrides. Same payload shape as ``forecasts.params`` + the derived
    columns, so the read path hands it to existing components unchanged.
    Empty default; Phase 2 populates.

Downgrade drops both columns — no data preserved.

Revision ID: 0009_type_curve_workspace
Revises: 0008_drop_survey_pipeline
Create Date: 2026-05-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_type_curve_workspace"
down_revision: str | Sequence[str] | None = "0008_drop_survey_pipeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "type_curves",
        sa.Column(
            "is_stale",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "type_curves",
        sa.Column(
            "forecast_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("type_curves", "forecast_overrides")
    op.drop_column("type_curves", "is_stale")
