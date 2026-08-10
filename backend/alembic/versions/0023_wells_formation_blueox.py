"""Add wells.formation_blueox + wells.basin_blueox.

Blue Ox standardized formation code (curated.wells_enriched.formation_blueox)
and its basin classification (basin_blueox = 'delaware'/'midland'), parallel to
the raw `formation` column. The app's basin-aware formation coloring/filtering
keys on these. Populated on the next well sync; existing rows land at NULL
until then. NULL is expected for unmapped wells / wells outside Delaware-Midland.

Revision ID: 0023_wells_formation_blueox
Revises: 0022_alignment_peak_ramp
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_wells_formation_blueox"
down_revision: str | Sequence[str] | None = "0022_alignment_peak_ramp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wells", sa.Column("formation_blueox", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "wells", sa.Column("basin_blueox", sa.String(length=16), nullable=True)
    )
    op.create_index("ix_wells_formation_blueox", "wells", ["formation_blueox"])
    op.create_index("ix_wells_basin_blueox", "wells", ["basin_blueox"])


def downgrade() -> None:
    op.drop_index("ix_wells_basin_blueox", table_name="wells")
    op.drop_index("ix_wells_formation_blueox", table_name="wells")
    op.drop_column("wells", "basin_blueox")
    op.drop_column("wells", "formation_blueox")
