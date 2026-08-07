"""Add DUC to the well_status enum.

The warehouse remap (warehouse_client/wells.py _STATUS_REMAP) has mapped
Novi Spud/DUC → "DUC" since the cutover, but the pg enum never gained the
member, so _validated_status downgraded every such well to UNKNOWN
(~2,900 wells in the current fetch scope, almost all NULL-completion
wellbores admitted by the scope-widening). Adding the value lets those
wells land as DUC on the next header sync; existing rows stay UNKNOWN
until then — the sync upsert rewrites status, so no data migration here.

AFTER 'PDP' keeps enum sort order roughly lifecycle-shaped. Postgres
cannot drop an enum value, so downgrade only reverts rows to UNKNOWN and
leaves the value in place (harmless — nothing writes DUC once the app
code is rolled back).

Revision ID: 0028_well_status_duc
Revises: 0027_well_lateral_closer_xy
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028_well_status_duc"
# Bumped from 0025 after the build-up (#38: 0026, 0027) merged first —
# this branch predated those migrations; single-head history restored.
down_revision: str | Sequence[str] | None = "0027_well_lateral_closer_xy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE well_status ADD VALUE IF NOT EXISTS 'DUC' AFTER 'PDP'")


def downgrade() -> None:
    op.execute("UPDATE wells SET status = 'UNKNOWN' WHERE status = 'DUC'")
