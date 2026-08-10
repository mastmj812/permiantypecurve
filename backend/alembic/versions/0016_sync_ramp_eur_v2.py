"""Re-run 0015 with explicit JSON cast for the params write.

0015 used ``sa.bindparam("params", type_=JSONB())`` on a ``text()``
statement. In some psycopg / SQLAlchemy combinations the bindparam
type isn't honored when the statement is executed with a plain dict —
the params write silently no-ops while the eur write succeeds, so the
column / JSONB stay out of sync and the preview-vs-stored EUR
mismatch persists.

This migration repeats 0015's logic with an explicit ``::jsonb`` cast
on a JSON-serialized string, which works on any psycopg version
without relying on the bindparam type machinery. Idempotent.

Revision ID: 0016_sync_ramp_eur_v2
Revises: 0015_sync_ramp_eur
Create Date: 2026-05-30
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.logging import get_logger
from app.forecasting.ramp_arps import compute_total_eur

revision: str = "0016_sync_ramp_eur_v2"
down_revision: str | Sequence[str] | None = "0015_sync_ramp_eur"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


log = get_logger("alembic.0016_sync_ramp_eur_v2")


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
        except Exception as e:
            log.warning(
                "eur_recompute_failed", id=str(row.id), error=str(e)[:200]
            )
            skipped += 1
            continue
        # Cast a JSON-serialized string into jsonb directly. Doesn't
        # depend on the bind-param type being honored by the driver,
        # which was the suspected failure mode in 0015.
        conn.execute(
            sa.text(
                "UPDATE forecasts SET eur = :eur, params = CAST(:params AS jsonb) "
                "WHERE id = :id"
            ),
            {
                "eur": float(new_eur),
                "params": json.dumps(params),
                "id": str(row.id),
            },
        )
        updated += 1

    log.info("ramp_eur_sync_v2_done", updated=updated, skipped=skipped)


def downgrade() -> None:
    # Same as 0015: pre-migration EUR / params are gone; rolling back
    # doesn't restore them. No-op.
    pass
