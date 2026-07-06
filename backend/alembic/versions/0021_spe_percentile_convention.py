"""Flip saved type-curve series to the SPE percentile convention.

The tool now uses the SPE / PRMS reserves orientation everywhere:
"P10" = HIGH case (exceeded by only 10% of wells), "P90" = LOW case,
P10 >= P50 >= P90. The aggregator previously emitted the statistical
orientation (p10 = 10th percentile = low), so every saved
``type_curves.series`` JSONB has its p10/p90 and p25/p75 entries
mirrored relative to what the labels now mean.

This migration swaps, for every stream block in both ``streams`` and
``observed_streams``:

  * the per-month arrays:        p10 <-> p90, p25 <-> p75
  * implied_eur_per_1000ft keys: p10 <-> p90, p25 <-> p75
  * fitted_eur_per_unit keys:    p10 <-> p90, p25 <-> p75 (when present)
  * fitted_per_percentile keys:  p10 <-> p90, p25 <-> p75 (when present)

``p50``, ``mean``, ``well_count``, and the published ``fitted`` block
are orientation-neutral and untouched. NOTE: the swap is an involution,
NOT idempotent — alembic's revision tracking guarantees single
application; do not re-run by hand.

Revision ID: 0021_spe_percentile_convention
Revises: 0020_recompute_gas_water_eur
Create Date: 2026-06-10
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from app.core.logging import get_logger

revision: str = "0021_spe_percentile_convention"
down_revision: str | Sequence[str] | None = "0020_recompute_gas_water_eur"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


log = get_logger("alembic.0021_spe_percentile_convention")

_SWAPS: tuple[tuple[str, str], ...] = (("p10", "p90"), ("p25", "p75"))
_NESTED_DICTS: tuple[str, ...] = (
    "implied_eur_per_1000ft",
    "fitted_eur_per_unit",
    "fitted_per_percentile",
)


def _swap_keys(d: dict[str, Any]) -> None:
    for a, b in _SWAPS:
        if a in d or b in d:
            d[a], d[b] = d.get(b), d.get(a)


def _flip_stream_block(block: dict[str, Any]) -> None:
    _swap_keys(block)  # the per-month p10/p25/p75/p90 arrays
    for key in _NESTED_DICTS:
        nested = block.get(key)
        if isinstance(nested, dict):
            _swap_keys(nested)


def _flip_series(series: dict[str, Any]) -> None:
    for streams_key in ("streams", "observed_streams"):
        streams = series.get(streams_key)
        if not isinstance(streams, dict):
            continue
        for block in streams.values():
            if isinstance(block, dict):
                _flip_stream_block(block)


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, series FROM type_curves WHERE series IS NOT NULL")
    ).fetchall()

    flipped = 0
    for row in rows:
        series = dict(row.series or {})
        if not series:
            continue
        _flip_series(series)
        conn.execute(
            sa.text(
                "UPDATE type_curves SET series = CAST(:series AS jsonb) "
                "WHERE id = :id"
            ),
            {"series": json.dumps(series), "id": str(row.id)},
        )
        flipped += 1

    log.info("spe_percentile_flip_done", flipped=flipped)


def downgrade() -> None:
    # The swap is its own inverse — run the same transform back.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, series FROM type_curves WHERE series IS NOT NULL")
    ).fetchall()
    for row in rows:
        series = dict(row.series or {})
        if not series:
            continue
        _flip_series(series)
        conn.execute(
            sa.text(
                "UPDATE type_curves SET series = CAST(:series AS jsonb) "
                "WHERE id = :id"
            ),
            {"series": json.dumps(series), "id": str(row.id)},
        )
