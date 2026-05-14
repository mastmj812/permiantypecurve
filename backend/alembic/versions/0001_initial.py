"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2.types import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Postgres ENUM types — created once, reused across columns.
WELL_STATUS = sa.Enum(
    "PDP", "PA", "SI", "TA", "INACTIVE", "UNKNOWN", name="well_status"
)
WELLSTICK_SOURCE = sa.Enum(
    "heel_to_bh", "surface_to_bh", "none", name="wellstick_source"
)
STREAM = sa.Enum("oil", "gas", "water", name="stream")
FIT_METHOD = sa.Enum("rate_cum", "rate_time", name="fit_method")
MODEL_TYPE = sa.Enum(
    "arps_exponential",
    "arps_hyperbolic",
    "arps_harmonic",
    "modified_hyperbolic",
    "duong",
    name="model_type",
)
NORMALIZATION_BASIS = sa.Enum(
    "per_lateral_ft", "per_proppant_lb", "per_well", name="normalization_basis"
)
ALIGNMENT_METHOD = sa.Enum("peak_month", name="alignment_method")
SYNC_ENTITY = sa.Enum(
    "well_headers", "production", "surveys", name="sync_entity"
)
SYNC_JOB_STATUS = sa.Enum(
    "pending", "running", "succeeded", "failed", "cancelled", name="sync_job_status"
)


def upgrade() -> None:
    # Extensions are created by infra/postgres/init.sql on first container
    # start. We still guard here for fresh DBs created outside compose.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    bind = op.get_bind()
    for enum_t in (
        WELL_STATUS,
        WELLSTICK_SOURCE,
        STREAM,
        FIT_METHOD,
        MODEL_TYPE,
        NORMALIZATION_BASIS,
        ALIGNMENT_METHOD,
        SYNC_ENTITY,
        SYNC_JOB_STATUS,
    ):
        enum_t.create(bind, checkfirst=True)

    # ---------------- wells ----------------
    op.create_table(
        "wells",
        sa.Column("api14", sa.String(14), primary_key=True),
        sa.Column("operator", sa.String(255)),
        sa.Column("formation", sa.String(64)),
        sa.Column("first_prod_date", sa.Date()),
        sa.Column(
            "vintage_year",
            sa.Integer(),
            sa.Computed("EXTRACT(YEAR FROM first_prod_date)::INT", persisted=True),
        ),
        sa.Column("lateral_ft", sa.Float()),
        sa.Column("proppant_lbs", sa.Float()),
        sa.Column("fluid_bbl", sa.Float()),
        sa.Column("stages", sa.Integer()),
        sa.Column("tvd_ft", sa.Float()),
        sa.Column("county", sa.String(64)),
        sa.Column("basin", sa.String(64)),
        sa.Column(
            "status",
            postgresql.ENUM(name="well_status", create_type=False),
            nullable=False,
            server_default="UNKNOWN",
        ),
        sa.Column("sh_geom", Geometry(geometry_type="POINT", srid=4326)),
        sa.Column("bh_geom", Geometry(geometry_type="POINT", srid=4326)),
        sa.Column("heel_lat", sa.Float()),
        sa.Column("heel_lon", sa.Float()),
        sa.Column("wellstick", Geometry(geometry_type="LINESTRING", srid=4326)),
        sa.Column(
            "wellstick_source",
            postgresql.ENUM(name="wellstick_source", create_type=False),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("raw_payload", postgresql.JSONB()),
    )
    op.create_index("ix_wells_operator", "wells", ["operator"])
    op.create_index("ix_wells_formation", "wells", ["formation"])
    op.create_index("ix_wells_first_prod_date", "wells", ["first_prod_date"])
    op.create_index("ix_wells_county", "wells", ["county"])
    op.create_index("ix_wells_basin", "wells", ["basin"])
    op.create_index("ix_wells_status", "wells", ["status"])
    op.create_index(
        "ix_wells_sh_geom_gist", "wells", ["sh_geom"], postgresql_using="gist"
    )
    op.create_index(
        "ix_wells_bh_geom_gist", "wells", ["bh_geom"], postgresql_using="gist"
    )
    op.create_index(
        "ix_wells_wellstick_gist", "wells", ["wellstick"], postgresql_using="gist"
    )

    # ---------------- directional_surveys ----------------
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

    # ---------------- production_monthly ----------------
    op.create_table(
        "production_monthly",
        sa.Column(
            "api14",
            sa.String(14),
            sa.ForeignKey("wells.api14", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("prod_date", sa.Date(), primary_key=True),
        sa.Column("oil_bbl", sa.Float()),
        sa.Column("gas_mcf", sa.Float()),
        sa.Column("water_bbl", sa.Float()),
        sa.Column("producing_days", sa.Integer()),
        sa.Column("rate_prodday_bopd", sa.Float()),
        sa.Column("rate_prodday_mcfd", sa.Float()),
        sa.Column("rate_prodday_bwpd", sa.Float()),
        sa.Column("rate_calday_bopd", sa.Float()),
        sa.Column("rate_calday_mcfd", sa.Float()),
        sa.Column("rate_calday_bwpd", sa.Float()),
        sa.Column("source", sa.String(32)),
    )
    # Convert to Timescale hypertable. 1-year chunks comfortably hold Permian
    # monthly volumes (~25k wells × 12 rows/yr ≈ 300k rows/chunk).
    op.execute(
        "SELECT create_hypertable("
        "  'production_monthly', 'prod_date',"
        "  chunk_time_interval => INTERVAL '1 year',"
        "  if_not_exists => TRUE"
        ")"
    )

    # ---------------- forecasts ----------------
    op.create_table(
        "forecasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "api14",
            sa.String(14),
            sa.ForeignKey("wells.api14", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stream", postgresql.ENUM(name="stream", create_type=False), nullable=False
        ),
        sa.Column(
            "model_type",
            postgresql.ENUM(name="model_type", create_type=False),
            nullable=False,
        ),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("qi", sa.Float()),
        sa.Column("di_initial", sa.Float()),
        sa.Column("b", sa.Float()),
        sa.Column("df_terminal", sa.Float()),
        sa.Column("eur", sa.Float()),
        sa.Column("peak_month_date", sa.Date()),
        sa.Column("peak_rate", sa.Float()),
        sa.Column(
            "fit_method",
            postgresql.ENUM(name="fit_method", create_type=False),
            nullable=False,
        ),
        sa.Column("fit_r2", sa.Float()),
        sa.Column("fit_rmse", sa.Float()),
        sa.Column("manual_override", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("api14", "stream", name="uq_forecasts_api14_stream"),
    )

    # ---------------- type_curves ----------------
    op.create_table(
        "type_curves",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("filter_spec", postgresql.JSONB(), nullable=False),
        sa.Column(
            "included_api14s",
            postgresql.ARRAY(sa.String(14)),
            nullable=False,
        ),
        sa.Column(
            "normalization_basis",
            postgresql.ENUM(name="normalization_basis", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "alignment_method",
            postgresql.ENUM(name="alignment_method", create_type=False),
            nullable=False,
        ),
        sa.Column("series", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "version_of",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("type_curves.id", ondelete="SET NULL"),
        ),
    )

    # ---------------- users ----------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(255)),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("sso_subject", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_users_sso_subject", "users", ["sso_subject"])

    # ---------------- sync_watermarks / sync_jobs ----------------
    op.create_table(
        "sync_watermarks",
        sa.Column(
            "entity",
            postgresql.ENUM(name="sync_entity", create_type=False),
            primary_key=True,
        ),
        sa.Column("scope_key", sa.String(255), primary_key=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entity",
            postgresql.ENUM(name="sync_entity", create_type=False),
            nullable=False,
        ),
        sa.Column("scope_key", sa.String(255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="sync_job_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(2000)),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_sync_jobs_entity_status", "sync_jobs", ["entity", "status"])


def downgrade() -> None:
    op.drop_index("ix_sync_jobs_entity_status", table_name="sync_jobs")
    op.drop_table("sync_jobs")
    op.drop_table("sync_watermarks")

    op.drop_index("ix_users_sso_subject", table_name="users")
    op.drop_table("users")

    op.drop_table("type_curves")
    op.drop_table("forecasts")
    op.drop_table("production_monthly")
    op.drop_table("directional_surveys")

    for idx in (
        "ix_wells_wellstick_gist",
        "ix_wells_bh_geom_gist",
        "ix_wells_sh_geom_gist",
        "ix_wells_status",
        "ix_wells_basin",
        "ix_wells_county",
        "ix_wells_first_prod_date",
        "ix_wells_formation",
        "ix_wells_operator",
    ):
        op.drop_index(idx, table_name="wells")
    op.drop_table("wells")

    bind = op.get_bind()
    for enum_t in (
        SYNC_JOB_STATUS,
        SYNC_ENTITY,
        ALIGNMENT_METHOD,
        NORMALIZATION_BASIS,
        MODEL_TYPE,
        FIT_METHOD,
        STREAM,
        WELLSTICK_SOURCE,
        WELL_STATUS,
    ):
        enum_t.drop(bind, checkfirst=True)
