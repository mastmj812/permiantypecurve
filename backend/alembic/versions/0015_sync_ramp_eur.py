"""Sync ramp params from columns into the JSONB, then recompute EUR.

Migration 0013 backfilled the ``qo`` and ``peak_index_months``
columns. Migration 0014 ran ``compute_total_eur(params)`` — which
reads the ramp params from the JSONB dict, not the columns — and so
for every pre-0013 row the dict still lacked the ramp keys,
``compute_total_eur`` fell back to pure Arps, and the stored EUR
didn't actually change. The preview endpoint, by contrast, receives
the ramp params from the frontend (which reads the columns), so
preview EUR differed from the stored EUR by exactly ramp_eur.

This migration fixes both layers in one pass:

  1. For every row where ``qo`` or ``peak_index_months`` is set on
     the column but missing from the JSONB, merge it in so future
     reads of ``params`` see the same values the columns hold.
  2. Recompute ``eur`` = ``compute_total_eur(merged_params)`` so the
     stored EUR matches what preview and patch produce going forward.

Idempotent — re-running produces the same merged params and the same
recomputed EUR.

Revision ID: 0015_sync_ramp_eur
Revises: 0014_recompute_total_eur
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.core.logging import get_logger
from app.forecasting.ramp_arps import compute_total_eur

revision: str = "0015_sync_ramp_eur"
down_revision: str | Sequence[str] | None = "0014_recompute_total_eur"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


log = get_logger("alembic.0015_sync_ramp_eur")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, model_type, params, qo, peak_index_months "
            "FROM forecasts WHERE params IS NOT NULL"
        )
    ).fetchall()

    updated = 0
    skipped = 0
    for row in rows:
        params = dict(row.params or {})
        # Pull ramp params from the columns into the dict when they're
        # set on one but missing on the other. Columns win — they're
        # the source of truth that 0013 wrote.
        if row.qo is not None:
            params["qo"] = float(row.qo)
        if row.peak_index_months is not None:
            params["peak_index_months"] = int(row.peak_index_months)
        if any(k not in params for k in ("qi", "Di", "b", "Df")):
            skipped += 1
            continue
        try:
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
            sa.text(
                "UPDATE forecasts SET eur = :eur, params = :params "
                "WHERE id = :id"
            ).bindparams(sa.bindparam("params", type_=JSONB())),
            {"eur": float(new_eur), "params": params, "id": row.id},
        )
        updated += 1

    log.info(
        "params_ramp_sync_done", updated=updated, skipped=skipped
    )


def downgrade() -> None:
    # Same as 0014: pre-migration EUR / params are gone; rolling
    # back doesn't restore them. No-op.
    pass
