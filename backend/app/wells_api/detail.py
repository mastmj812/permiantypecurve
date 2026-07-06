"""Per-well detail + filter facets.

  GET /api/wells/{api10}                 → full attributes + geometry as GeoJSON
  GET /api/wells/filters/operators?q=    → type-ahead for the operator multi-select
  GET /api/wells/filters/facets          → formation + status counts for the left rail
  GET /api/wells/wellsticks?api10s=...   → GeoJSON FeatureCollection for the review-tab map
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.db.models import Well, WellStatus
from app.db.session import get_session
from app.wells_api.filters import FilterSpec, parse_filter_query

router = APIRouter(prefix="/wells", tags=["wells"])


class WellDetail(BaseModel):
    api10: str
    operator: str | None
    formation: str | None
    formation_blueox: str | None = None
    basin_blueox: str | None = None
    first_prod_date: date | None
    vintage_year: int | None
    lateral_ft: float | None
    proppant_lbs: float | None
    fluid_bbl: float | None
    tvd_ft: float | None
    county: str | None
    basin: str | None
    status: WellStatus
    sh_lat: float | None
    sh_lon: float | None
    bh_lat: float | None
    bh_lon: float | None
    last_synced_at: datetime | None


class WellDetailLite(BaseModel):
    """Lightweight bundle for the gun-barrel inspect modal AND for
    synthesizing stub Review-table rows for short-history wells that
    don't yet have a forecast (pending cohort transfer).

    Originally just the fields the gun-barrel renderer + tooltip
    needed (sh/bh coords, TVD, lateral_ft, formation, first_prod_date).
    Extended with vintage_year / county / novi_oil_eur so the Review
    table can render unfit wells with the same column set as fit
    wells — additive change, existing callers ignore the new keys.
    """

    api10: str
    name: str | None
    formation: str | None
    formation_blueox: str | None = None
    basin_blueox: str | None = None
    operator: str | None
    lateral_ft: float | None
    tvd_ft: float | None
    sh_lat: float | None
    sh_lon: float | None
    bh_lat: float | None
    bh_lon: float | None
    first_prod_date: date | None
    vintage_year: int | None = None
    county: str | None = None
    novi_oil_eur: float | None = None


@router.get("/wellsticks")
def wellsticks_geojson(
    api10s: str = Query(..., description="CSV of api10s to fetch wellsticks for"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return wellstick LineStrings as a GeoJSON FeatureCollection.

    Used by the Review-tab map to show only the wells currently being
    reviewed, colored by EUR/ft (the join happens client-side against
    the forecasts list the page already has in state). Skips wells
    whose wellstick is null (surveys not yet synced) — they just don't
    appear on the map; their row still shows in the table.

    The api10s list is bounded by the same 500-well cap as the
    forecast workflow, so we don't worry about query-string overflow.
    """
    ids = [a.strip() for a in api10s.split(",") if a.strip()]
    if not ids:
        return {"type": "FeatureCollection", "features": []}
    rows = session.execute(
        select(
            Well.api10,
            Well.formation,
            Well.formation_blueox,
            Well.basin_blueox,
            Well.operator,
            func.ST_AsGeoJSON(Well.wellstick).label("geom"),
        ).where(Well.api10.in_(ids), Well.wellstick.is_not(None))
    ).all()
    features = [
        {
            "type": "Feature",
            "geometry": json.loads(r.geom),
            "properties": {
                "api10": r.api10,
                "formation": r.formation,
                "formation_blueox": r.formation_blueox,
                "basin_blueox": r.basin_blueox,
                "operator": r.operator,
            },
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "features": features}


@router.get("/details", response_model=list[WellDetailLite])
def well_details(
    api10s: str = Query(..., description="CSV of api10s to fetch detail bundles for"),
    session: Session = Depends(get_session),
) -> list[WellDetailLite]:
    """Batched well-detail bundle for the gun-barrel inspect modal.

    Single query against the wells table; reuses the same ST_X/ST_Y
    lat/lon extraction pattern the single-well endpoint uses below.
    Registered before ``/{api10}`` so FastAPI matches the literal path
    instead of treating "details" as an api10 value.
    """
    ids = [a.strip() for a in api10s.split(",") if a.strip()]
    if not ids:
        return []
    rows = session.execute(
        select(
            Well.api10,
            Well.name,
            Well.formation,
            Well.formation_blueox,
            Well.basin_blueox,
            Well.operator,
            Well.lateral_ft,
            Well.tvd_ft,
            func.ST_Y(Well.sh_geom).label("sh_lat"),
            func.ST_X(Well.sh_geom).label("sh_lon"),
            func.ST_Y(Well.bh_geom).label("bh_lat"),
            func.ST_X(Well.bh_geom).label("bh_lon"),
            Well.first_prod_date,
            Well.vintage_year,
            Well.county,
            Well.novi_oil_eur,
        ).where(Well.api10.in_(ids))
    ).all()
    return [
        WellDetailLite(
            api10=r.api10,
            name=r.name,
            formation=r.formation,
            formation_blueox=r.formation_blueox,
            basin_blueox=r.basin_blueox,
            operator=r.operator,
            lateral_ft=float(r.lateral_ft) if r.lateral_ft is not None else None,
            tvd_ft=float(r.tvd_ft) if r.tvd_ft is not None else None,
            sh_lat=r.sh_lat,
            sh_lon=r.sh_lon,
            bh_lat=r.bh_lat,
            bh_lon=r.bh_lon,
            first_prod_date=r.first_prod_date,
            vintage_year=int(r.vintage_year) if r.vintage_year is not None else None,
            county=r.county,
            novi_oil_eur=(
                float(r.novi_oil_eur) if r.novi_oil_eur is not None else None
            ),
        )
        for r in rows
    ]


@router.get("/{api10}", response_model=WellDetail)
def well_detail(api10: str, session: Session = Depends(get_session)) -> WellDetail:
    # Pull lat/lon via ST_X / ST_Y so we don't ship full WKB to the client.
    row = session.execute(
        select(
            Well.api10,
            Well.operator,
            Well.formation,
            Well.formation_blueox,
            Well.basin_blueox,
            Well.first_prod_date,
            Well.vintage_year,
            Well.lateral_ft,
            Well.proppant_lbs,
            Well.fluid_bbl,
            Well.tvd_ft,
            Well.county,
            Well.basin,
            Well.status,
            func.ST_Y(Well.sh_geom).label("sh_lat"),
            func.ST_X(Well.sh_geom).label("sh_lon"),
            func.ST_Y(Well.bh_geom).label("bh_lat"),
            func.ST_X(Well.bh_geom).label("bh_lon"),
            Well.last_synced_at,
        ).where(Well.api10 == api10)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"well {api10} not found")

    return WellDetail(
        api10=row.api10,
        operator=row.operator,
        formation=row.formation,
        formation_blueox=row.formation_blueox,
        basin_blueox=row.basin_blueox,
        first_prod_date=row.first_prod_date,
        vintage_year=row.vintage_year,
        lateral_ft=row.lateral_ft,
        proppant_lbs=row.proppant_lbs,
        fluid_bbl=row.fluid_bbl,
        tvd_ft=row.tvd_ft,
        county=row.county,
        basin=row.basin,
        status=row.status,
        sh_lat=row.sh_lat,
        sh_lon=row.sh_lon,
        bh_lat=row.bh_lat,
        bh_lon=row.bh_lon,
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


def _facet_clauses_excluding(
    spec: FilterSpec, *, exclude_attr: str
) -> list[ColumnElement[bool]]:
    """Build the WHERE clauses for one facet's count.

    Standard faceted-search pattern: each facet's counts are computed
    with every filter applied EXCEPT the user's selection on that facet
    itself. Otherwise, selecting "WOLFCAMP A" would zero out every other
    formation's count and the user couldn't see "if I switch to
    WOLFCAMP B, how many wells would match?" anymore.

    Setting a tuple-valued FilterSpec field to `()` drops it from the
    WHERE chain because `to_sqlalchemy_clauses()` guards each clause on
    truthiness. That works for statuses too — replacing with `()` here
    is a deliberate override of the PDP default.
    """
    return replace(spec, **{exclude_attr: ()}).to_sqlalchemy_clauses()


@router.get("/filters/facets", response_model=FilterFacets)
def filter_facets(
    spec: FilterSpec = Depends(parse_filter_query),
    session: Session = Depends(get_session),
) -> FilterFacets:
    """Return counts for each facet under the current filter state.

    For the formations / statuses / counties facets, the count is computed
    with the user's selection on THAT facet excluded — so each row reads
    as "would-match if you toggled this on". Universe coverage is ensured
    by joining the per-facet counts against the global distinct-values
    set; formations that exist in the DB but match zero wells under the
    current filters appear with count=0 (the frontend renders those
    faded).

    lateral_ft / first_prod_year extremes stay global so the slider
    bounds don't jitter as the user filters.
    """
    # --- universe of values (independent of filters) ---
    # Facet universe + counts use the standardized formation_blueox codes
    # (the filter dimension; raw `formation` is retained elsewhere for display).
    all_formations: list[str] = [
        v for (v,) in session.execute(
            select(Well.formation_blueox)
            .where(Well.formation_blueox.isnot(None))
            .distinct()
        ).all()
    ]
    all_counties: list[str] = [
        v for (v,) in session.execute(
            select(Well.county).where(Well.county.isnot(None)).distinct()
        ).all()
    ]
    all_statuses: list[Any] = [
        v for (v,) in session.execute(select(Well.status).distinct()).all()
    ]

    def _counts_under(clauses: list[ColumnElement[bool]], col: Any) -> dict[Any, int]:
        stmt = select(col, func.count()).group_by(col)
        for c in clauses:
            stmt = stmt.where(c)
        return {r[0]: int(r[1]) for r in session.execute(stmt).all() if r[0] is not None}

    formations_count = _counts_under(
        _facet_clauses_excluding(spec, exclude_attr="formations"), Well.formation_blueox
    )
    statuses_count = _counts_under(
        _facet_clauses_excluding(spec, exclude_attr="statuses"), Well.status
    )
    counties_count = _counts_under(
        _facet_clauses_excluding(spec, exclude_attr="counties"), Well.county
    )

    formations_out = sorted(
        (
            FacetCount(value=v, count=formations_count.get(v, 0))
            for v in all_formations
        ),
        key=lambda f: (-f.count, f.value),
    )
    counties_out = sorted(
        (
            FacetCount(value=v, count=counties_count.get(v, 0))
            for v in all_counties
        ),
        key=lambda f: (-f.count, f.value),
    )
    statuses_out = [
        FacetCount(
            value=s.value if hasattr(s, "value") else str(s),
            count=statuses_count.get(s, 0),
        )
        for s in all_statuses
        if s is not None
    ]

    extremes = session.execute(
        select(
            func.min(Well.lateral_ft),
            func.max(Well.lateral_ft),
            func.min(Well.vintage_year),
            func.max(Well.vintage_year),
        )
    ).first()

    return FilterFacets(
        formations=formations_out,
        statuses=statuses_out,
        counties=counties_out,
        lateral_ft_min=float(extremes[0]) if extremes and extremes[0] is not None else None,
        lateral_ft_max=float(extremes[1]) if extremes and extremes[1] is not None else None,
        first_prod_year_min=int(extremes[2]) if extremes and extremes[2] is not None else None,
        first_prod_year_max=int(extremes[3]) if extremes and extremes[3] is not None else None,
    )
