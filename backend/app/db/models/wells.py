from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Computed,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enum_helpers import pg_enum


class WellStatus(str, enum.Enum):
    PDP = "PDP"          # producing
    PA = "PA"            # plugged & abandoned
    SI = "SI"            # shut-in
    TA = "TA"            # temporarily abandoned
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


class WellstickSource(str, enum.Enum):
    HEEL_TO_BH = "heel_to_bh"
    SURFACE_TO_BH = "surface_to_bh"
    NONE = "none"


class Well(Base):
    __tablename__ = "wells"

    api14: Mapped[str] = mapped_column(String(14), primary_key=True)

    # Well/lease name as reported by Enverus (e.g. "STATE 36 #1H"). Free-form
    # — varies by operator. Indexed because the review/forecast tables sort
    # and search by it.
    name: Mapped[str | None] = mapped_column(String(255), index=True)

    operator: Mapped[str | None] = mapped_column(String(255), index=True)
    formation: Mapped[str | None] = mapped_column(String(64), index=True)
    first_prod_date: Mapped[date | None] = mapped_column(Date, index=True)
    # vintage_year is derived from first_prod_date so it stays in lockstep —
    # used by the map's "first prod date" filter and vintage histograms.
    vintage_year: Mapped[int | None] = mapped_column(
        Integer,
        Computed("EXTRACT(YEAR FROM first_prod_date)::INT", persisted=True),
    )

    lateral_ft: Mapped[float | None] = mapped_column(Float)
    proppant_lbs: Mapped[float | None] = mapped_column(Float)
    fluid_bbl: Mapped[float | None] = mapped_column(Float)
    stages: Mapped[int | None] = mapped_column(Integer)
    tvd_ft: Mapped[float | None] = mapped_column(Float)

    county: Mapped[str | None] = mapped_column(String(64), index=True)
    basin: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[WellStatus] = mapped_column(
        pg_enum(WellStatus, name="well_status"),
        default=WellStatus.UNKNOWN,
        nullable=False,
        index=True,
    )

    sh_geom: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326)
    )
    bh_geom: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326)
    )

    wellstick: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326)
    )
    wellstick_source: Mapped[WellstickSource] = mapped_column(
        pg_enum(WellstickSource, name="wellstick_source"),
        default=WellstickSource.NONE,
        nullable=False,
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        # GIST indexes are essential for bbox/lasso queries from the map.
        Index("ix_wells_sh_geom_gist", "sh_geom", postgresql_using="gist"),
        Index("ix_wells_bh_geom_gist", "bh_geom", postgresql_using="gist"),
        Index("ix_wells_wellstick_gist", "wellstick", postgresql_using="gist"),
    )
