"""Per-well detail + filter facets.

  GET /api/wells/{api14}                 → full attributes + geometry as GeoJSON
  GET /api/wells/filters/operators?q=    → type-ahead for the operator multi-select
  GET /api/wells/filters/facets          → formation + status counts for the left rail
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Well, WellstickSource, WellStatus
from app.db.session import get_session

router = APIRouter(prefix="/wells", tags=["wells"])


class WellDetail(BaseModel):
    api14: str
    operator: str | None
    formation: str | None
    first_prod_date: date | None
    vintage_year: int | None
    lateral_ft: float | None
    proppant_lbs: float | None
    fluid_bbl: float | None
    stages: int | None
    tvd_ft: float | None
    county: str | None
    basin: str | None
    status: WellStatus
    wellstick_source: WellstickSource
    sh_lat: float | None
    sh_lon: float | None
    bh_lat: float | None
    bh_lon: float | None
    heel_lat: float | None
    heel_lon: float | None
    last_synced_at: datetime | None


@router.get("/{api14}", response_model=WellDetail)
def well_detail(api14: str, session: Session = Depends(get_session)) -> WellDetail:
    # Pull lat/lon via ST_X / ST_Y so we don't ship full WKB to the client.
    row = session.execute(
        select(
            Well.api14,
            Well.operator,
            Well.formation,
            Well.first_prod_date,
            Well.vintage_year,
            Well.lateral_ft,
            Well.proppant_lbs,
            Well.fluid_bbl,
            Well.stages,
            Well.tvd_ft,
            Well.county,
            Well.basin,
            Well.status,
            Well.wellstick_source,
            func.ST_Y(Well.sh_geom).label("sh_lat"),
            func.ST_X(Well.sh_geom).label("sh_lon"),
            func.ST_Y(Well.bh_geom).label("bh_lat"),
            func.ST_X(Well.bh_geom).label("bh_lon"),
            Well.heel_lat,
            Well.heel_lon,
            Well.last_synced_at,
        ).where(Well.api14 == api14)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"well {api14} not found")

    return WellDetail(
        api14=row.api14,
        operator=row.operator,
        formation=row.formation,
        first_prod_date=row.first_prod_date,
        vintage_year=row.vintage_year,
        lateral_ft=row.lateral_ft,
        proppant_lbs=row.proppant_lbs,
        fluid_bbl=row.fluid_bbl,
        stages=row.stages,
        tvd_ft=row.tvd_ft,
        county=row.county,
        basin=row.basin,
        status=row.status,
        wellstick_source=row.wellstick_source,
        sh_lat=row.sh_lat,
        sh_lon=row.sh_lon,
        bh_lat=row.bh_lat,
        bh_lon=row.bh_lon,
        heel_lat=row.heel_lat,
        heel_lon=row.heel_lon,
        last_synced_at=row.last_synced_at,
    )


# ---------------- filter facets ----------------

class OperatorMatch(BaseModel):
    operator: str
    count: int


@router.get("/filters/operators", response_model=list[OperatorMatch])
def operator_typeahead(
    q: str = Query(default="", description="case-insensitive substring"),
    limit: int = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[OperatorMatch]:
    stmt = (
        select(Well.operator, func.count().label("n"))
        .where(Well.operator.isnot(None))
        .group_by(Well.operator)
        .order_by(func.count().desc(), Well.operator.asc())
        .limit(limit)
    )
    if q:
        # ILIKE with leading-wildcard isn't index-friendly, but at single-
        # user scale (≤ a few hundred unique operators in the Permian) this
        # is fine. Drop in pg_trgm + gin later if needed.
        stmt = stmt.where(Well.operator.ilike(f"%{q}%"))
    rows = session.execute(stmt).all()
    return [OperatorMatch(operator=r.operator, count=r.n) for r in rows]


class FacetCount(BaseModel):
    value: str
    count: int


class FilterFacets(BaseModel):
    formations: list[FacetCount]
    statuses: list[FacetCount]
    counties: list[FacetCount]
    lateral_ft_min: float | None
    lateral_ft_max: float | None
    first_prod_year_min: int | None
    first_prod_year_max: int | None


@router.get("/filters/facets", response_model=FilterFacets)
def filter_facets(session: Session = Depends(get_session)) -> FilterFacets:
    formations_rows = session.execute(
        select(Well.formation, func.count())
        .where(Well.formation.isnot(None))
        .group_by(Well.formation)
        .order_by(func.count().desc())
    ).all()
    statuses_rows = session.execute(
        select(Well.status, func.count()).group_by(Well.status)
    ).all()
    counties_rows = session.execute(
        select(Well.county, func.count())
        .where(Well.county.isnot(None))
        .group_by(Well.county)
        .order_by(func.count().desc())
    ).all()
    extremes = session.execute(
        select(
            func.min(Well.lateral_ft),
            func.max(Well.lateral_ft),
            func.min(Well.vintage_year),
            func.max(Well.vintage_year),
        )
    ).first()

    def _to_facets(rows: list[Any]) -> list[FacetCount]:
        out: list[FacetCount] = []
        for r in rows:
            val = r[0]
            count = r[1]
            if val is None:
                continue
            out.append(
                FacetCount(value=val.value if hasattr(val, "value") else str(val), count=count)
            )
        return out

    return FilterFacets(
        formations=_to_facets(formations_rows),
        statuses=_to_facets(statuses_rows),
        counties=_to_facets(counties_rows),
        lateral_ft_min=float(extremes[0]) if extremes and extremes[0] is not None else None,
        lateral_ft_max=float(extremes[1]) if extremes and extremes[1] is not None else None,
        first_prod_year_min=int(extremes[2]) if extremes and extremes[2] is not None else None,
        first_prod_year_max=int(extremes[3]) if extremes and extremes[3] is not None else None,
    )
