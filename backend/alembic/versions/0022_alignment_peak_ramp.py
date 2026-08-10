"""add peak_ramp to alignment_method enum

Peak-aligned aggregation with a ramp lookback: wells align at a common
peak month (the cohort-median ramp length) so qi/Di statistics aren't
smeared by staggered peaks, while the months before the common peak
carry each well's real ramp. See loader.load_wells_with_forecast.

Revision ID: 0022_alignment_peak_ramp
Revises: 0021_spe_percentile_convention
Create Date: 2026-06-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_alignment_peak_ramp"
down_revision: str | Sequence[str] | None = "0021_spe_percentile_convention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres rejects ALTER TYPE ... ADD VALUE inside a transaction block;
    # autocommit_block escapes the surrounding transaction (same pattern
    # as 0002).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE alignment_method ADD VALUE IF NOT EXISTS 'peak_ramp'")


def downgrade() -> None:
    # Dropping an enum value requires recreating the type; no-op (same
    # rationale as 0002).
    pass
