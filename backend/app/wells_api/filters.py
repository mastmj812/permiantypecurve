"""Filter spec — the single source of truth for what's selectable.

Used by:
  * /api/wells/tiles/{z}/{x}/{y}.mvt   (tile WHERE clause)
  * /api/wells/select                  (spatial query WHERE clause)
  * /api/wells/search                  (planned)

Keeping the parsing + the SQL fragment in one module means a new filter
column lands in three places (parse, SQL, model) rather than five.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from fastapi import Query
from sqlalchemy import ColumnElement

from app.db.models import Well, WellStatus

# Default vintage window for the left rail (mirrors the brief).
DEFAULT_VINTAGE_YEARS_BACK = 10


def _split_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


@dataclass(frozen=True)
class FilterSpec:
    formations: tuple[str, ...] = ()
    operators: tuple[str, ...] = ()
    counties: tuple[str, ...] = ()
    statuses: tuple[WellStatus, ...] = (WellStatus.PDP,)
    first_prod_start: date | None = None
    first_prod_end: date | None = None
    lateral_min_ft: float | None = None
    lateral_max_ft: float | None = None

    def to_sqlalchemy_clauses(self) -> list[ColumnElement[bool]]:
        """Compose into the AND chain that goes into WHERE. Returns a list so
        callers can extend with their own clauses (the tile endpoint adds an
        ST_Intersects, the selection endpoint adds an ST_Contains/Within)."""
        clauses: list[ColumnElement[bool]] = []
        if self.formations:
            clauses.append(Well.formation.in_(self.formations))
        if self.operators:
            clauses.append(Well.operator.in_(self.operators))
        if self.counties:
            clauses.append(Well.county.in_(self.counties))
        if self.statuses:
            clauses.append(Well.status.in_(self.statuses))
        if self.first_prod_start is not None:
            clauses.append(Well.first_prod_date >= self.first_prod_start)
        if self.first_prod_end is not None:
            clauses.append(Well.first_prod_date <= self.first_prod_end)
        if self.lateral_min_ft is not None:
            clauses.append(Well.lateral_ft >= self.lateral_min_ft)
        if self.lateral_max_ft is not None:
            clauses.append(Well.lateral_ft <= self.lateral_max_ft)
        return clauses


def _parse_statuses(raw: str | None) -> tuple[WellStatus, ...]:
    if not raw:
        return (WellStatus.PDP,)  # brief default
    out: list[WellStatus] = []
    for item in _split_csv(raw):
        try:
            out.append(WellStatus(item.upper()))
        except ValueError:
            # Skip unknown status codes silently rather than 400 — keeps the
            # frontend tolerant when a new code shows up in a future build.
            continue
    return tuple(out) or (WellStatus.PDP,)


def parse_filter_query(
    formations: str | None = Query(default=None, description="CSV of formation names"),
    operators: str | None = Query(default=None, description="CSV of operator names"),
    counties: str | None = Query(default=None),
    statuses: str | None = Query(default=None, description="CSV of WellStatus codes"),
    first_prod_start: date | None = Query(default=None, alias="first_prod_start"),
    first_prod_end: date | None = Query(default=None, alias="first_prod_end"),
    lateral_min_ft: float | None = Query(default=None, ge=0),
    lateral_max_ft: float | None = Query(default=None, ge=0),
) -> FilterSpec:
    """FastAPI dependency — turns query params into a FilterSpec."""
    return FilterSpec(
        formations=tuple(_split_csv(formations)),
        operators=tuple(_split_csv(operators)),
        counties=tuple(_split_csv(counties)),
        statuses=_parse_statuses(statuses),
        first_prod_start=first_prod_start,
        first_prod_end=first_prod_end,
        lateral_min_ft=lateral_min_ft,
        lateral_max_ft=lateral_max_ft,
    )


def filter_spec_dict(spec: FilterSpec) -> dict[str, Any]:
    """JSONable view of the spec — used when persisting saved type curves
    (step 6) and for summary-endpoint response echoes."""
    return {
        "formations": list(spec.formations),
        "operators": list(spec.operators),
        "counties": list(spec.counties),
        "statuses": [s.value for s in spec.statuses],
        "first_prod_start": spec.first_prod_start.isoformat() if spec.first_prod_start else None,
        "first_prod_end": spec.first_prod_end.isoformat() if spec.first_prod_end else None,
        "lateral_min_ft": spec.lateral_min_ft,
        "lateral_max_ft": spec.lateral_max_ft,
    }
