"""wells.lateral_closer_xy_ft — Novi same-zone spacing passthrough.

Novi WellSpacing ``LateralCloserXY`` via
``curated.wells_enriched.lateral_closer_xy_ft``: same-zone lateral
offset distance (ft, XY plane), AS-OF-FIRST-PRODUCTION — not current
spacing. NULL = well absent from WellSpacing (standalone candidate).
SENTINEL: exactly 2800.0 is Novi's default/cap when no same-zone
neighbor existed at first production (~12% of rows, also the column
max) — not a real measured distance. The map filter treats NULL and
the sentinel as one "unbounded / no-neighbor" class behind an explicit
include toggle (see wells_api/filters.py::SPACING_SENTINEL_FT).

Existing rows stay NULL until the next wells sync repopulates from the
warehouse.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_well_lateral_closer_xy"
down_revision: str | Sequence[str] | None = "0026_type_curve_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wells",
        sa.Column("lateral_closer_xy_ft", sa.Float(), nullable=True),
    )
    # Filter predicate on the map tile / selection queries — same
    # rationale as the existing lateral_ft/first_prod_date indexes.
    op.create_index(
        "ix_wells_lateral_closer_xy_ft", "wells", ["lateral_closer_xy_ft"]
    )


def downgrade() -> None:
    op.drop_index("ix_wells_lateral_closer_xy_ft", table_name="wells")
    op.drop_column("wells", "lateral_closer_xy_ft")
