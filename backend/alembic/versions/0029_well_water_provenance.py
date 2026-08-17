"""wells.water_source + wells.wor_cv — water-provenance passthrough.

Synced from ``curated.water_data_quality`` (one row per api10; applied
live 2026-08-17): ``water_source`` in {'measured','calculated',
'indeterminate','insufficient'}, NULL = well absent from the matview.
TX public water is mostly vendor-CALCULATED (a static WOR x oil — 83.6%
of TX public-water horizontals have dead-flat WOR), so water fits /
type curves on those wells inherit a fabricated stream.

CONVENTION OF RECORD (2026-08-17): FLAG ONLY — badge and filter;
nothing is auto-excluded from any fit or cohort.

``wor_cv`` is the monthly-WOR coefficient of variation (near-zero =
dead-flat, the calculated signature) — diagnostic display only.

Existing rows stay NULL until the next wells sync repopulates from the
warehouse (same rollout as lateral_closer_xy_ft in 0027).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_well_water_provenance"
down_revision: str | Sequence[str] | None = "0028_well_status_duc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wells",
        sa.Column("water_source", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "wells",
        sa.Column("wor_cv", sa.Float(), nullable=True),
    )
    # Filter predicate on the map tile / selection queries — same
    # rationale as the lateral_closer_xy_ft index in 0027.
    op.create_index("ix_wells_water_source", "wells", ["water_source"])


def downgrade() -> None:
    op.drop_index("ix_wells_water_source", table_name="wells")
    op.drop_column("wells", "wor_cv")
    op.drop_column("wells", "water_source")
