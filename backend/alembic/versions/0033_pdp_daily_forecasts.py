"""Deal-scoped PDP forecasts from seller VDR daily production.

* ``vdr_daily_production`` — local copy of warehouse ``vdr.well_daily``
  (engineering_db sql/53) per data room.
* ``pdp_forecasts`` — one row per (deal, api10, stream); deliberately NOT
  the global ``forecasts`` table (that one feeds type-curve cohorts on the
  warehouse monthly basis). Method of record: app.forecasting.daily.
* ``deals.pdp_config`` — {vdr_id, api10s, uptime_overrides}.

Revision ID: 0033_pdp_daily_forecasts
Revises: 0032_well_dev_scenario
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033_pdp_daily_forecasts"
down_revision: str | Sequence[str] | None = "0032_well_dev_scenario"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("pdp_config", postgresql.JSONB(), nullable=True))

    op.create_table(
        "vdr_daily_production",
        sa.Column("vdr_id", sa.String(64), primary_key=True),
        sa.Column("api10", sa.String(10), primary_key=True),
        sa.Column("prod_date", sa.Date(), primary_key=True),
        sa.Column("well_name", sa.String(255)),
        sa.Column("reserve_category", sa.String(32)),
        sa.Column("oil_bbl", sa.Float()),
        sa.Column("gas_mcf", sa.Float()),
        sa.Column("water_bbl", sa.Float()),
        sa.Column("tbg_psi", sa.Float()),
        sa.Column("csg_psi", sa.Float()),
        sa.Column("choke", sa.Float()),
        sa.Column("bhp_psi", sa.Float()),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "pdp_forecasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deals.id", ondelete="CASCADE", name="fk_pdp_forecasts_deal_id_deals"),
            nullable=False,
        ),
        sa.Column("vdr_id", sa.String(64), nullable=False),
        sa.Column("api10", sa.String(10), nullable=False),
        sa.Column("stream", postgresql.ENUM(name="stream", create_type=False), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("model_type", sa.String(32), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("qi", sa.Float(), nullable=False),
        sa.Column("di_initial", sa.Float(), nullable=False),
        sa.Column("b", sa.Float(), nullable=False),
        sa.Column("df_terminal", sa.Float(), nullable=False),
        sa.Column("anchor_date", sa.Date(), nullable=False),
        sa.Column("fit_start_date", sa.Date()),
        sa.Column("data_through", sa.Date(), nullable=False),
        sa.Column("uptime_factor", sa.Float(), nullable=False),
        sa.Column("uptime_basis", postgresql.JSONB(), nullable=False),
        sa.Column("fit_r2_log", sa.Float()),
        sa.Column("fit_rmse_log", sa.Float()),
        sa.Column("n_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tail_ratio", sa.Float()),
        sa.Column("cum_to_date", sa.Float(), nullable=False),
        sa.Column("remaining", sa.Float(), nullable=False),
        sa.Column("eur", sa.Float(), nullable=False),
        sa.Column("review_flags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("breaks", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("diagnostics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("manual_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "deal_id", "api10", "stream", name="uq_pdp_forecasts_deal_api10_stream"
        ),
    )
    op.create_index("ix_pdp_forecasts_deal_id", "pdp_forecasts", ["deal_id"])


def downgrade() -> None:
    op.drop_index("ix_pdp_forecasts_deal_id", table_name="pdp_forecasts")
    op.drop_table("pdp_forecasts")
    op.drop_table("vdr_daily_production")
    op.drop_column("deals", "pdp_config")
