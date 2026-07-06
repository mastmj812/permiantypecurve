"""Recompute gas/water EURs without the economic-limit truncation.

The batch autoforecast endpoint's ``ForecastConfigBody`` defaulted
``economic_limit_mcfd=30`` / ``economic_limit_bwpd=50`` — leftovers from
before the technical-EUR decision — while every other recompute path
(PATCH, TC overrides, CLI ``--refit``) used the canonical 0.0 from
``app.forecasting.types``. Gas/water EURs written by the batch path were
therefore truncated at the rate floor and *path-dependent*: saving an
override with identical params recomputed at limit 0 and the stored EUR
jumped.

The API defaults now match the canonical 0.0 (see ForecastConfigBody);
this migration recomputes ``eur`` from each row's own params over the
raw 50-yr horizon so existing gas/water rows match what every path
computes today. Params are NOT modified, so manual/locked rows keep
their engineer-chosen fit — only the derived EUR scalar is normalized
(for rows already written at limit 0 the recompute is a no-op). Oil
rows (whose limit default was always 0) are untouched. Idempotent.

Revision ID: 0020_recompute_gas_water_eur
Revises: 0019_wells_subbasin
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core.logging import get_logger
from app.forecasting.ramp_arps import compute_total_eur

revision: str = "0020_recompute_gas_water_eur"
down_revision: str | Sequence[str] | None = "0019_wells_subbasin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


log = get_logger("alembic.0020_recompute_gas_water_eur")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, model_type, params, qo, peak_index_months "
            "FROM forecasts "
            "WHERE params IS NOT NULL AND stream IN ('gas', 'water')"
        )
    ).fetchall()

    updated = 0
    skipped = 0
    for row in rows:
        # Same param-merge convention as migration 0016: row-level ramp
        # columns win when the params JSONB predates them.
        params = dict(row.params or {})
        if row.qo is not None:
            params.setdefault("qo", float(row.qo))
        if row.peak_index_months is not None:
            params.setdefault("peak_index_months", int(row.peak_index_months))
        if any(k not in params for k in ("qi", "Di", "b", "Df")):
            skipped += 1
            continue
        try:
            # Defaults: horizon_years=50, economic_limit=0 — the raw
            # technical EUR every code path now computes.
            new_eur = compute_total_eur(
                model_type=row.model_type,
                params=params,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "eur_recompute_failed", id=str(row.id), error=str(e)[:200]
            )
            skipped += 1
            continue
        conn.execute(
            sa.text("UPDATE forecasts SET eur = :eur WHERE id = :id"),
            {"eur": float(new_eur), "id": str(row.id)},
        )
        updated += 1

    log.info("gas_water_eur_recompute_done", updated=updated, skipped=skipped)


def downgrade() -> None:
    # The truncated pre-migration EURs are gone; rolling back doesn't
    # restore them (and shouldn't — they were the bug). No-op.
    pass
