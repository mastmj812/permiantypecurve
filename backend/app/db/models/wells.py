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


class Well(Base):
    __tablename__ = "wells"

    # Novi wellbore identifier (10 chars). Primary key as of the cutover
    # from direct-Enverus to engineering_db reads (migration 0010).
    api10: Mapped[str] = mapped_column(String(10), primary_key=True)

    # Enverus completion identifier (14 chars). Nullable secondary
    # column for cross-reference back to Enverus history; not load-
    # bearing for joins anywhere in the app. May be None for wells that
    # pre-date Enverus coverage or weren't matched at curated-layer join
    # time.
    api14: Mapped[str | None] = mapped_column(String(14), index=True)

    # Well/lease name as reported by Novi/Enverus. Free-form — varies by
    # operator. Indexed because the review/forecast tables sort and
    # search by it.
    name: Mapped[str | None] = mapped_column(String(255), index=True)

    operator: Mapped[str | None] = mapped_column(String(255), index=True)
    formation: Mapped[str | None] = mapped_column(String(64), index=True)
    first_prod_date: Mapped[date | None] = mapped_column(Date, index=True)
    # vintage_year is derived from first_prod_date so it stays in lockstep —
    # used by the map's "first prod date" filter and vintage histograms.
    # Wells without a first_prod_date (fresh completions, no production
    # reported yet) get NULL here, which implicitly excludes them from
    # vintage-bucketed displays. Intentional: no production = nothing to
    # forecast or include in a type curve.
    vintage_year: Mapped[int | None] = mapped_column(
        Integer,
        Computed("EXTRACT(YEAR FROM first_prod_date)::INT", persisted=True),
    )

    lateral_ft: Mapped[float | None] = mapped_column(Float)
    proppant_lbs: Mapped[float | None] = mapped_column(Float)
    fluid_bbl: Mapped[float | None] = mapped_column(Float)
    tvd_ft: Mapped[float | None] = mapped_column(Float)
    # Novi's 50-yr oil EUR (curated.wells_enriched.eur_50yr_oil_bbl).
    # Surfaced in the Review table as a benchmark against the app's own
    # autoforecast EUR. Pure passthrough — no app-side recompute. May be
    # NULL for wells Novi hasn't forecasted (e.g. PA / very early
    # vintage with no production yet).
    novi_oil_eur: Mapped[float | None] = mapped_column(Float)

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

    # 4-point LINESTRING built upstream in
    # engineering_db.curated.wells.wellstick_geom (SHL -> LP -> MP -> BHL).
    # The legacy wellstick_source enum was dropped in the cutover — only
    # one source remains.
    wellstick: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326)
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # GIST indexes are essential for bbox/lasso queries from the map.
        Index("ix_wells_sh_geom_gist", "sh_geom", postgresql_using="gist"),
        Index("ix_wells_bh_geom_gist", "bh_geom", postgresql_using="gist"),
        Index("ix_wells_wellstick_gist", "wellstick", postgresql_using="gist"),
    )
