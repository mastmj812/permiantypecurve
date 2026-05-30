"""Selection endpoints — turn a drawn polygon / bbox / api10 list into a
summary the right-drawer can render.

Two shapes:

  POST /api/wells/select
    body: {polygon: GeoJSON | null, bbox: [w,s,e,n] | null, filters: {...}}
    → {api10s, summary, filter_echo}
    Used for lasso and box draws. Server intersects the well geometry
    (wellstick, fall back to sh_geom) with the drawn shape.

  POST /api/wells/summary
    body: {api10s: [...]}
    → {summary}
    Used after click-add/remove so the drawer refreshes without re-doing the
    spatial query.

Both enforce the 500-well hard cap. The drawer renders a warning at 300.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Well
from app.db.session import get_session
from app.wells_api.filters import FilterSpec, filter_spec_dict
from app.wells_api.summary import (
    HARD_SELECTION_CAP,
    SelectionSummary,
    WellSummaryRow,
    build_summary,
)

router = APIRouter(prefix="/wells", tags=["wells"])
log = get_logger("api.wells.selection")


# ---------------- request/response models ----------------

class FilterSpecBody(BaseModel):
    formations: list[str] = Field(default_factory=list)
    operators: list[str] = Field(default_factory=list)
    counties: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=lambda: ["PDP"])
    first_prod_start: date | None = None
    first_prod_end: date | None = None
    lateral_min_ft: float | None = None
    lateral_max_ft: float | None = None
    # Allow-list pasted in the FilterPanel. Must be honored here too so a
    # box/lasso select after the user has narrowed the map to a specific
    # well set doesn't reach back and pick up filtered-out wells inside
    # the same geometry.
    api10s: list[str] = Field(default_factory=list)

    def to_spec(self) -> FilterSpec:
        from app.db.models import WellStatus

        return FilterSpec(
            formations=tuple(self.formations),
            operators=tuple(self.operators),
            counties=tuple(self.counties),
            statuses=tuple(
                WellStatus(s) for s in self.statuses if s in WellStatus.__members__
            )
            or (WellStatus.PDP,),
            first_prod_start=self.first_prod_start,
            first_prod_end=self.first_prod_end,
            lateral_min_ft=self.lateral_min_ft,
            lateral_max_ft=self.lateral_max_ft,
            api10s=tuple(self.api10s),
        )


class SelectRequest(BaseModel):
    polygon: dict[str, Any] | None = Field(
        default=None, description="GeoJSON Polygon geometry (NOT a Feature)"
    )
    bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="[west, south, east, north] in lon/lat"
    )
    filters: FilterSpecBody = Field(default_factory=FilterSpecBody)


class SummaryRequest(BaseModel):
    api10s: list[str] = Field(min_length=0, max_length=HARD_SELECTION_CAP + 1)


class SummaryResponse(BaseModel):
    count: int
    median_lateral_ft: float | None
    vintage_histogram: dict[int, int]
    operators_top5: list[tuple[str, int]]
    formations: dict[str, int]
    exceeds_soft_cap: bool
    exceeds_hard_cap: bool


class SelectResponse(BaseModel):
    api10s: list[str]
    summary: SummaryResponse
    filter_echo: dict[str, Any]


def _to_response(s: SelectionSummary) -> SummaryResponse:
    return SummaryResponse(
        count=s.count,
        median_lateral_ft=s.median_lateral_ft,
        vintage_histogram=s.vintage_histogram,
        operators_top5=s.operators_top5,
        formations=s.formations,
        exceeds_soft_cap=s.exceeds_soft_cap,
        exceeds_hard_cap=s.exceeds_hard_cap,
    )


def _rows_for_api10s(session: Session, api10s: list[str]) -> list[WellSummaryRow]:
    if not api10s:
        return []
    res = session.execute(
        select(
            Well.api10,
            Well.formation,
            Well.operator,
            func.extract("year", Well.first_prod_date).label("vintage"),
            Well.lateral_ft,
        ).where(Well.api10.in_(api10s))
    ).all()
    return [
        WellSummaryRow(
            api10=r.api10,
            formation=r.formation,
            operator=r.operator,
            first_prod_year=int(r.vintage) if r.vintage is not None else None,
            lateral_ft=float(r.lateral_ft) if r.lateral_ft is not None else None,
        )
        for r in res
    ]


# ---------------- spatial selection ----------------

@router.post("/select", response_model=SelectResponse)
def select_wells(
    req: SelectRequest, session: Session = Depends(get_session)
) -> SelectResponse:
    if req.polygon is None and req.bbox is None:
        raise HTTPException(
            status_code=400, detail="must provide either polygon or bbox"
        )

    spec = req.filters.to_spec()
    filter_clauses = spec.to_sqlalchemy_clauses()

    # Build the spatial predicate. We test against wellstick first (most
    # accurate); if a well has no wellstick we fall back to sh_geom so
    # surface-located wells aren't invisible to selection.
    if req.polygon is not None:
        # GeoJSON arrives as Python dict — psycopg passes it as text to
        # ST_GeomFromGeoJSON which wants a JSON string, not a record.
        geom_sql = text(
            "ST_SetSRID(ST_GeomFromGeoJSON(:geojson), 4326)"
        ).bindparams(geojson=json.dumps(req.polygon))
    else:
        assert req.bbox is not None
        w, s, e, n = req.bbox
        geom_sql = text(
            "ST_SetSRID(ST_MakeEnvelope(:w, :s, :e, :n, 4326), 4326)"
        ).bindparams(w=w, s=s, e=e, n=n)

    candidate = func.coalesce(Well.wellstick, Well.sh_geom)
    spatial = func.ST_Intersects(candidate, geom_sql)

    stmt = (
        select(
            Well.api10,
            Well.formation,
            Well.operator,
            func.extract("year", Well.first_prod_date).label("vintage"),
            Well.lateral_ft,
        )
        .where(and_(spatial, *filter_clauses))
        # Cap pulls one over so the response can flag exceeds_hard_cap.
        .limit(HARD_SELECTION_CAP + 1)
    )

    rows = session.execute(stmt).all()
    summary_rows = [
        WellSummaryRow(
            api10=r.api10,
            formation=r.formation,
            operator=r.operator,
            first_prod_year=int(r.vintage) if r.vintage is not None else None,
            lateral_ft=float(r.lateral_ft) if r.lateral_ft is not None else None,
        )
        for r in rows
    ]
    summary = build_summary(summary_rows)

    # Hard-cap behavior: surface ALL matching api10s up to cap+1 so the
    # client can show "513 wells selected — reduce to ≤ 500".
    api10s = [r.api10 for r in summary_rows]

    return SelectResponse(
        api10s=api10s,
        summary=_to_response(summary),
        filter_echo=filter_spec_dict(spec),
    )


# ---------------- summary-only refresh ----------------

@router.post("/summary", response_model=SummaryResponse)
def summary_for_api10s(
    req: SummaryRequest, session: Session = Depends(get_session)
) -> SummaryResponse:
    if len(req.api10s) > HARD_SELECTION_CAP:
        # Don't 400 — still return the summary so the UI can render the
        # over-cap warning rather than failing closed.
        log.warning("summary_over_hard_cap", count=len(req.api10s))
    rows = _rows_for_api10s(session, req.api10s)
    return _to_response(build_summary(rows))
