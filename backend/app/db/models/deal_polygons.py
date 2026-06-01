"""Deal-acreage polygons sourced from user-uploaded shapefiles.

Each row is one polygon (or multipolygon) feature from a shapefile,
linked optionally to a Deal so the Map tab can toggle visibility per
deal and the Type Curve slide preview can auto-render only the
curve's own deal acreage.

The geometry is stored as MULTIPOLYGON in EPSG:4326 (the app-wide
convention shared with ``wells.sh_geom`` / ``wells.wellstick``).
Source-CRS reprojection happens at upload time via PostGIS
``ST_Transform``; the .prj sidecar tells us the source EPSG.

``attributes`` is the original DBF attribute-table row preserved
verbatim — the admin modal renders it so the user can figure out
which polygon is which when assigning to a deal.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DealPolygon(Base):
    __tablename__ = "deal_polygons"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Nullable: a freshly-uploaded polygon lives unlinked until the
    # user assigns it to a deal via the admin modal. ON DELETE SET
    # NULL keeps polygons around if a deal is removed.
    deal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("deals.id", ondelete="SET NULL"),
        index=True,
    )
    # Best-effort label from the shapefile attributes — the first
    # non-numeric attribute, falling back to "Polygon {fid}". Helps
    # the user identify rows in the admin modal.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326)
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    source_file: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_deal_polygons_geom_gist", "geom", postgresql_using="gist"
        ),
    )
