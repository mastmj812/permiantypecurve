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
from typing import Annotated, Any

from fastapi import Query
from sqlalchemy import ColumnElement, and_, or_

from app.db.models import Well, WellStatus

# Default vintage window for the left rail (mirrors the brief).
DEFAULT_VINTAGE_YEARS_BACK = 10

# Novi WellSpacing no-neighbor sentinel: LateralCloserXY is capped at
# exactly 2800.0 when no same-zone neighbor existed at first production
# (~12% of rows; also the column max). Confirmed with Novi 2026-07-14 —
# see engineering_db sql/06_curated_derived.sql. The spacing filter
# treats sentinel rows together with NULL (absent from WellSpacing) as
# one "unbounded / no-neighbor" class behind an explicit include flag,
# so a wide-spacing floor like ">= 1500 ft" can't silently sweep in
# parent wells whose 2800 is a cap, not a measurement.
SPACING_SENTINEL_FT = 2800.0


def _split_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def _escape_like(s: str) -> str:
    """Escape LIKE metacharacters so user input matches literally."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    # Same-zone spacing (Novi LateralCloserXY, ft, as-of-first-prod).
    # Bounds apply ONLY to wells with REAL spacing (non-null and not the
    # 2800 sentinel). When either bound is set, unbounded/no-neighbor
    # wells (NULL or sentinel) drop out unless spacing_include_unbounded
    # re-admits them. With no bounds set the flag is inert (all wells
    # pass, today's behavior).
    spacing_min_ft: float | None = None
    spacing_max_ft: float | None = None
    spacing_include_unbounded: bool = False
    # Case-insensitive substring match on the well/lease name (Novi/
    # Enverus free-form). Substring, not exact/multiselect — names vary
    # per wellbore ("UNIVERSITY 7-43 2H"), so "contains" is the only
    # useful grain. User-typed % _ \ are escaped (matched literally).
    well_name_contains: str | None = None
    # Explicit api10 allow-list. When non-empty, only wells with one of
    # these api10s pass — pasted from an external tool's well-list so
    # the engineer can recreate the same selection here and forecast.
    api10s: tuple[str, ...] = ()

    def to_sqlalchemy_clauses(self) -> list[ColumnElement[bool]]:
        """Compose into the AND chain that goes into WHERE. Returns a list so
        callers can extend with their own clauses (the tile endpoint adds an
        ST_Intersects, the selection endpoint adds an ST_Contains/Within)."""
        clauses: list[ColumnElement[bool]] = []
        if self.formations:
            # `formations` now carries standardized formation_blueox codes
            # (the facet universe is formation_blueox), so filter on that
            # column. Raw `formation` is retained but no longer the filter key.
            clauses.append(Well.formation_blueox.in_(self.formations))
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
        if self.spacing_min_ft is not None or self.spacing_max_ft is not None:
            col = Well.lateral_closer_xy_ft
            bounded_parts: list[ColumnElement[bool]] = [
                col.isnot(None),
                col != SPACING_SENTINEL_FT,
            ]
            if self.spacing_min_ft is not None:
                bounded_parts.append(col >= self.spacing_min_ft)
            if self.spacing_max_ft is not None:
                bounded_parts.append(col <= self.spacing_max_ft)
            bounded = and_(*bounded_parts)
            if self.spacing_include_unbounded:
                clauses.append(
                    or_(bounded, col.is_(None), col == SPACING_SENTINEL_FT)
                )
            else:
                clauses.append(bounded)
        if self.well_name_contains:
            clauses.append(
                Well.name.ilike(
                    f"%{_escape_like(self.well_name_contains)}%", escape="\\"
                )
            )
        if self.api10s:
            clauses.append(Well.api10.in_(self.api10s))
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
    formations: Annotated[
        str | None, Query(description="CSV of formation names")
    ] = None,
    operators: Annotated[
        str | None, Query(description="CSV of operator names")
    ] = None,
    counties: Annotated[str | None, Query()] = None,
    statuses: Annotated[
        str | None, Query(description="CSV of WellStatus codes")
    ] = None,
    first_prod_start: Annotated[
        date | None, Query(alias="first_prod_start")
    ] = None,
    first_prod_end: Annotated[
        date | None, Query(alias="first_prod_end")
    ] = None,
    lateral_min_ft: Annotated[float | None, Query(ge=0)] = None,
    lateral_max_ft: Annotated[float | None, Query(ge=0)] = None,
    spacing_min_ft: Annotated[float | None, Query(ge=0)] = None,
    spacing_max_ft: Annotated[float | None, Query(ge=0)] = None,
    spacing_include_unbounded: Annotated[
        bool,
        Query(
            description=(
                "With a spacing bound set, also include wells with no "
                "same-zone neighbor (NULL / 2800-sentinel LateralCloserXY)"
            )
        ),
    ] = False,
    well_name_contains: Annotated[
        str | None,
        Query(
            max_length=120,
            description="Case-insensitive substring match on well/lease name",
        ),
    ] = None,
    api10s: Annotated[
        str | None, Query(description="CSV of 10-digit API numbers (allow-list)")
    ] = None,
) -> FilterSpec:
    """FastAPI dependency — turns query params into a FilterSpec.

    Using ``Annotated[..., Query(...)]`` with real ``None`` defaults
    (rather than ``= Query(default=None)``) keeps the function naturally
    callable as plain Python — unfilled params resolve to ``None``
    instead of a ``Query()`` sentinel object that explodes when
    downstream code tries to ``.split()`` it. Critical for unit tests
    that exercise the parser without standing up a FastAPI request.
    """
    return FilterSpec(
        formations=tuple(_split_csv(formations)),
        operators=tuple(_split_csv(operators)),
        counties=tuple(_split_csv(counties)),
        statuses=_parse_statuses(statuses),
        first_prod_start=first_prod_start,
        first_prod_end=first_prod_end,
        lateral_min_ft=lateral_min_ft,
        lateral_max_ft=lateral_max_ft,
        spacing_min_ft=spacing_min_ft,
        spacing_max_ft=spacing_max_ft,
        spacing_include_unbounded=spacing_include_unbounded,
        well_name_contains=(well_name_contains or "").strip() or None,
        api10s=tuple(_split_csv(api10s)),
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
        "spacing_min_ft": spec.spacing_min_ft,
        "spacing_max_ft": spec.spacing_max_ft,
        "spacing_include_unbounded": spec.spacing_include_unbounded,
        "well_name_contains": spec.well_name_contains,
        "api10s": list(spec.api10s),
    }
