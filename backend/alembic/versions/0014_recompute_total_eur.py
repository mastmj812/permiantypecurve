"""Recompute Forecast.eur as ramp_eur + arps_eur for every row.

Migration 0013 added qo + peak_index_months and backfilled them, but
left the existing `eur` column untouched — meaning rows fit before
0013 (the vast majority of the table) still carry pure-Arps EUR while
rows fit after 0013 (and the preview / patch paths) emit total EUR
(arps + ramp). The mismatch becomes visible the moment you click
"preview" on a pre-0013 forecast: the EUR jumps because preview
applies compute_total_eur while the stored value is still arps-only.

This migration loops every forecast row whose ``params`` carries the
full parameter set and replaces ``eur`` with the result of
``compute_total_eur``. Idempotent: re-running it on a row that
already has total_eur produces the same number (round-trip is exact
for the closed-form Arps + trapezoid ramp).

Revision ID: 0014_recompute_total_eur
Revises: 0013_forecast_ramp_params
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.logging import get_logger
from app.forecasting.ramp_arps import compute_total_eur

revision: str = "0014_recompute_total_eur"
down_revision: str | Sequence[str] | None = "0013_forecast_ramp_params"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


log = get_logger("alembic.0014_recompute_total_eur")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, model_type, params FROM forecasts WHERE params IS NOT NULL")
    ).fetchall()

    updated = 0
    skipped = 0
    for row in rows:
        params = row.params or {}
        # compute_total_eur needs qi/Di/b/Df at minimum. Ramp prefix
        # (qo + peak_index_months) is optional — falls back to pure
        # Arps when missing, which is exactly what already-correct
        # pre-ramp rows store anyway, so no harm in running them too.
        if any(k not in params for k in ("qi", "Di", "b", "Df")):
            skipped += 1
            continue
        try:
            new_eur = compute_total_eur(
                model_type=row.model_type,
                params=params,
            )
        except Exception as e:
            log.warning("eur_recompute_failed", id=str(row.id), error=str(e)[:200])
            skipped += 1
            continue
        conn.execute(
            sa.text("UPDATE forecasts SET eur = :eur WHERE id = :id"),
            {"eur": float(new_eur), "id": row.id},
        )
        updated += 1

    log.info("eur_recompute_done", updated=updated, skipped=skipped)


def downgrade() -> None:
    # Can't recover the pre-migration eur values — and the post-
    # migration values are the correct ones anyway. No-op.
    pass
