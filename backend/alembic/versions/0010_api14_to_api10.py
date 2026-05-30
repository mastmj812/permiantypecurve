"""api14 -> api10 cutover.

Schema-only migration for the cutover from direct-Enverus ingest to
``engineering_db`` warehouse reads. Per the cutover plan
(``project_permian_type_curve_cutover`` memory), the app realigns its
primary key with Novi's wellbore-level identifier (10 char) instead of
Enverus's completion-level identifier (14 char).

**Strategy: option B, truncate-and-resync.** Wipes wells /
production_monthly / forecasts / type_curves; the next warehouse-driven
orchestrator run repopulates wells + production_monthly from
``engineering_db.curated.*``. Saved forecasts and type curves are
intentionally not preserved — user opted for a clean slate over
migration-time api14 -> api10 backfill complexity (handful of curves;
acceptable to redo with higher-quality Novi data).

**Column drops** baked into this migration:
- ``wells.raw_payload`` (forensics shifts to engineering_db raw_* schemas)
- ``wells.wellstick_source`` + enum type (single Novi-derived source now)
- ``wells.stages`` (TX/NM operators are not required to report stages;
  the column was always sparse/zero and never load-bearing downstream)
- ``production_monthly.source`` (single warehouse source now)
- ``production_monthly.rate_prodday_*`` (3 cols; never read downstream
  per the cutover audit; the canonical rate columns are rate_calday_*)

**Scope deliberately limited to schema.** Ingest / orchestrator / test
code still references api14 in places (see cutover memory step
sequence); those updates land in steps 6+ when warehouse_client is
wired into the orchestrator. Between this migration and step 6, the
legacy enverus_client orchestrator path is broken at runtime — do not
trigger sync until step 6 lands.

Revision ID: 0010_api14_to_api10
Revises: 0009_type_curve_workspace
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision = "0010_api14_to_api10"
down_revision = "0009_type_curve_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Truncate api14-keyed data.
    #
    # TRUNCATE wells CASCADE wipes production_monthly + forecasts via
    # FK ondelete=CASCADE. type_curves stores api14s in an ARRAY column
    # (not a FK), so it needs to be truncated separately.
    # ------------------------------------------------------------------
    op.execute("TRUNCATE TABLE wells CASCADE")
    op.execute("TRUNCATE TABLE type_curves CASCADE")

    # ------------------------------------------------------------------
    # 2. Drop dependent FKs (so the wells PK can be dropped next).
    # ------------------------------------------------------------------
    op.drop_constraint(
        "fk_production_monthly_api14_wells",
        "production_monthly",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_forecasts_api14_wells",
        "forecasts",
        type_="foreignkey",
    )

    # ------------------------------------------------------------------
    # 3. wells: swap api14 PK -> api10 PK; api14 becomes nullable
    #    indexed secondary; drop retired columns + the wellstick_source
    #    enum type.
    # ------------------------------------------------------------------
    op.drop_constraint("pk_wells", "wells", type_="primary")
    op.add_column(
        "wells",
        sa.Column("api10", sa.String(length=10), nullable=False),
    )
    op.create_primary_key("pk_wells", "wells", ["api10"])
    op.alter_column(
        "wells",
        "api14",
        existing_type=sa.String(length=14),
        nullable=True,
    )
    op.create_index("ix_wells_api14", "wells", ["api14"])

    op.drop_column("wells", "raw_payload")
    op.drop_column("wells", "stages")
    op.drop_column("wells", "wellstick_source")
    # The wellstick_source column was the only consumer of the enum.
    op.execute("DROP TYPE IF EXISTS wellstick_source")

    # ------------------------------------------------------------------
    # 4. production_monthly: swap PK + FK; drop retired columns.
    #
    # This is a Timescale hypertable. ALTER TABLE operations propagate
    # to chunks automatically. The new composite PK still includes the
    # partitioning column (prod_date), which is Timescale's hard
    # requirement for unique constraints on hypertables.
    # ------------------------------------------------------------------
    op.drop_constraint(
        "pk_production_monthly", "production_monthly", type_="primary"
    )
    op.drop_column("production_monthly", "api14")
    op.drop_column("production_monthly", "rate_prodday_bopd")
    op.drop_column("production_monthly", "rate_prodday_mcfd")
    op.drop_column("production_monthly", "rate_prodday_bwpd")
    op.drop_column("production_monthly", "source")
    op.add_column(
        "production_monthly",
        sa.Column("api10", sa.String(length=10), nullable=False),
    )
    op.create_primary_key(
        "pk_production_monthly",
        "production_monthly",
        ["api10", "prod_date"],
    )
    op.create_foreign_key(
        "fk_production_monthly_api10_wells",
        "production_monthly",
        "wells",
        ["api10"],
        ["api10"],
        ondelete="CASCADE",
    )

    # ------------------------------------------------------------------
    # 5. forecasts: swap api14 column, FK, UQ.
    # ------------------------------------------------------------------
    op.drop_constraint(
        "uq_forecasts_api14_stream", "forecasts", type_="unique"
    )
    op.drop_column("forecasts", "api14")
    op.add_column(
        "forecasts",
        sa.Column("api10", sa.String(length=10), nullable=False),
    )
    op.create_foreign_key(
        "fk_forecasts_api10_wells",
        "forecasts",
        "wells",
        ["api10"],
        ["api10"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_forecasts_api10_stream", "forecasts", ["api10", "stream"]
    )

    # ------------------------------------------------------------------
    # 6. type_curves: swap the api14 array for an api10 array.
    #    forecast_overrides JSONB stays (was wiped by TRUNCATE; the
    #    JSONB top-level keys are api10 strings going forward).
    # ------------------------------------------------------------------
    op.drop_column("type_curves", "included_api14s")
    op.add_column(
        "type_curves",
        sa.Column(
            "included_api10s",
            ARRAY(sa.String(length=10)),
            nullable=False,
        ),
    )


def downgrade() -> None:
    # The cutover is one-way. Reconstructing api14 from curated state is
    # non-trivial because curated.wells already collapses Enverus
    # completion events to one row per wellbore. To roll back, restore
    # the database from a pre-0010 backup.
    raise NotImplementedError(
        "The api14 -> api10 cutover is one-way. To roll back, restore "
        "the database from a pre-0010 backup."
    )
