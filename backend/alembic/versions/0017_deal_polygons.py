"""Create deal_polygons table.

Stores user-uploaded acreage polygons (one row per shapefile feature),
linked optionally to a Deal. PostGIS MULTIPOLYGON in EPSG:4326 with a
GIST index for the bbox / polygon lookups the Map tab will issue.

Revision ID: 0017_deal_polygons
Revises: 0016_sync_ramp_eur_v2
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_deal_polygons"
down_revision: str | Sequence[str] | None = "0016_sync_ramp_eur_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deal_polygons",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "deal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "geom",
            Geometry(geometry_type="MULTIPOLYGON", srid=4326),
            nullable=False,
        ),
        sa.Column(
            "attributes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_file", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_deal_polygons_deal_id", "deal_polygons", ["deal_id"])
    op.create_index(
        "ix_deal_polygons_geom_gist",
        "deal_polygons",
        ["geom"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_index("ix_deal_polygons_geom_gist", table_name="deal_polygons")
    op.drop_index("ix_deal_polygons_deal_id", table_name="deal_polygons")
    op.drop_table("deal_polygons")
