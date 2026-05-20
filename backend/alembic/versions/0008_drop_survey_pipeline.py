"""Retire the survey-fetch pipeline.

Phase 2 of the LateralLine migration. Enverus's `LateralLine` column on
the wells endpoint replaced the heel-from-station survey computation
(99%+ coverage on horizontal wells across our four-county scope).

What this drops:
  * ``directional_surveys`` table (and its FK to wells via api14)
  * ``wells.heel_lat`` / ``wells.heel_lon`` columns — written only by the
    retired heel computation; nothing in the app reads them, the
    wellstick LineString's first coordinate is the heel for any
    consumer that ever needs it back.

What stays:
  * ``SyncEntity.SURVEYS`` enum value — historical ``sync_jobs`` rows
    still reference it; dropping the enum value would orphan that
    history. The orchestrator no longer creates new SURVEYS rows.
  * ``WellstickSource.HEEL_TO_BH`` and ``WellstickSource.SURFACE_TO_BH``
    — both still in use (LateralLine wells = HEEL_TO_BH; fallback wells
    with sh+BH = SURFACE_TO_BH; never-touched wells stay NONE).

Downgrade restores the schema but not the row data. Re-running the
retired survey-fetch pipeline would need to be coded back in.

Revision ID: 0008_drop_survey_pipeline
Revises: 0007_deals
Create Date: 2026-05-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_drop_survey_pipeline"
down_revision: str | Sequence[str] | None = "0007_deals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the dependent table first (FK to wells.api14).
    op.drop_table("directional_surveys")

    # Then the unused heel coordinate columns. Float, nullable — easy
    # to restore on downgrade, no data to migrate (nothing reads them).
    op.drop_column("wells", "heel_lat")
    op.drop_column("wells", "heel_lon")


def downgrade() -> None:
    # Recreate heel coords first so the survey table's foreign key
    # target shape matches the original.
    op.add_column("wells", sa.Column("heel_lon", sa.Float(), nullable=True))
    op.add_column("wells", sa.Column("heel_lat", sa.Float(), nullable=True))

    # Recreate the survey-stations table with the original 0001 schema.
    # No data is restored — re-syncing from Enverus would be required
    # to repopulate, and the orchestrator's survey-fetch phase would
    # need to come back too.
    op.create_table(
        "directional_surveys",
        sa.Column(
            "api14",
            sa.String(14),
            sa.ForeignKey("wells.api14", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("station_seq", sa.Integer(), primary_key=True),
        sa.Column("md_ft", sa.Float(), nullable=False),
        sa.Column("inclination_deg", sa.Float(), nullable=False),
        sa.Column("azimuth_deg", sa.Float()),
        sa.Column("tvd_ft", sa.Float()),
        sa.Column("lat", sa.Float()),
        sa.Column("lon", sa.Float()),
    )
