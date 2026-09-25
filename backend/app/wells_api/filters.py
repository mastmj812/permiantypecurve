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
from sqlalchemy import ColumnElement, Integer, Numeric, and_, cast, exists, func, or_, select

from app.db.models import Well, WellStatus

# Default vintage window for the left rail (mirrors the brief).
DEFAULT_VINTAGE_YEARS_BACK = 10

# Novi WellSpacing no-neighbor sentinel: LateralCloserXY is capped at
# exactly 2800.0 when no same-zone neighbor existed at first production
# (~12% of rows; also the column max). Confirmed with Novi 2026-07-14 —
# see engineering_db sql/06_curated_derived.sql. 2800 is a cap, not a
# measurement, so a wide-spacing floor like ">= 1500 ft" must never
# sweep it in as if it were a wide-spaced well.
SPACING_SENTINEL_FT = 2800.0

# Water-stream provenance classes (curated.water_data_quality.water_source
# synced onto wells.water_source). The four real values plus 'no_data'
# for NULL (well absent from the matview — no producing months). TX
# public water is mostly vendor-CALCULATED (static WOR x oil), so
# 'calculated' wells carry a fabricated water stream. FLAG ONLY by
# convention of record (2026-08-17): the filter defaults to ALL classes
# (empty tuple = no clause) and nothing is auto-excluded from any fit
# or cohort.
WATER_SOURCE_VALUES: frozenset[str] = frozenset(
    {"measured", "calculated", "indeterminate", "insufficient"}
)
WATER_SOURCE_NO_DATA = "no_data"

# Development scenario (engineering_db curated.dev_scenario, sql/50),
# synced onto wells.scenario_*. Classes in precedence order, plus 'no_data'
# for NULL (well absent from the view: no production / no stick). The class
# is parent-side and NOT censored (see Well.scenario_class).
SCENARIO_CLASS_VALUES: tuple[str, ...] = (
    "sandwich",
    "topfill",
    "underfill",
    "codev_stack",
    "standalone",
)
SCENARIO_NO_DATA = "no_data"
# Bench-pair parent filter ("WCA_1 beneath LSSH") reads the per-bench jsonb
# directly: no vertical window, no shielding -- naming the pair IS the
# vertical spec. The lateral-offset gate stays. CROSS-REPO CONTRACT: equals
# the literal in engineering_db sql/50 and scripts/find_analogs.py
# OFFSET_GATE_FT -- change all three or none.
PARENT_OFFSET_GATE_FT = 660.0
PARENT_SIDES: tuple[str, ...] = ("any", "above", "below")

# Spacing splits the well population into three disjoint classes, and
# the min/max range binds ONLY the first:
#   measured     — real LateralCloserXY (non-null, != 2800)
#   no_neighbor  — exactly the 2800 cap: known standalone
#   no_data      — NULL: absent from Novi WellSpacing, unknown
# The two no-spacing classes are independently admitted, and their
# include flags are live WITH OR WITHOUT a range set — an unchecked box
# always removes wells. (Before 2026-08 a single `include_unbounded`
# flag covered both classes and was inert unless a bound was set, so an
# unchecked box silently did nothing; buildup._resolve_spacing_classes
# still reproduces that for snapshots persisted under the old scheme.)


def _split_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


def escape_like(s: str) -> str:
    """Escape LIKE metacharacters so user input matches literally.

    Public: the tile endpoint's raw-SQL mirror (tiles._build_filter_sql)
    must build the identical pattern."""
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
    # Bounds apply ONLY to the `measured` class; the two no-spacing
    # classes are admitted (or not) independently of any range — see the
    # class taxonomy above SPACING_SENTINEL_FT.
    spacing_min_ft: float | None = None
    spacing_max_ft: float | None = None
    spacing_include_no_neighbor: bool = True
    spacing_include_no_data: bool = True
    # Case-insensitive substring match on the well/lease name (Novi/
    # Enverus free-form). Substring, not exact/multiselect — names vary
    # per wellbore ("UNIVERSITY 7-43 2H"), so "contains" is the only
    # useful grain. User-typed % _ \ are escaped (matched literally).
    well_name_contains: str | None = None
    # Explicit api10 allow-list. When non-empty, only wells with one of
    # these api10s pass — pasted from an external tool's well-list so
    # the engineer can recreate the same selection here and forecast.
    api10s: tuple[str, ...] = ()
    # Water-provenance classes to admit. Empty = ALL (no clause — the
    # flag-only default; the no-op tile URL and its ETag are unchanged).
    # Non-empty = only wells whose water_source is in the tuple;
    # WATER_SOURCE_NO_DATA admits the NULLs.
    water_sources: tuple[str, ...] = ()
    # Development scenario. Empty tuples / None = no clause (the no-op tile
    # URL and ETag are unchanged).
    #   scenario_classes   admit these classes; 'no_data' admits NULL
    #   scenario_benches   subject bench, TVD-corrected (wells.scenario_bench
    #                      -- NOT formation_blueox; the two can differ)
    #   parent_benches     bench-pair: the well has a parent (> 180 d older)
    #                      in any of these benches within the 660-ft offset
    #                      gate; no vertical window, no shielding
    #   parent_side        'above' / 'below' narrows parent_benches (inert
    #                      without it)
    #   parent_dtvd_max_ft |dTVD| cap on that parent (inert without it)
    #   parent_age_min/max_days  with parent_benches: that bench's youngest
    #                      / oldest parent age; without: the class-level
    #                      youngest / oldest qualifying vertical parent
    scenario_classes: tuple[str, ...] = ()
    scenario_benches: tuple[str, ...] = ()
    parent_benches: tuple[str, ...] = ()
    parent_side: str = "any"
    parent_dtvd_max_ft: float | None = None
    parent_age_min_days: int | None = None
    parent_age_max_days: int | None = None

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
        # Spacing: OR together the admitted classes. Skipped entirely only
        # when nothing is constrained (no range AND both classes in), which
        # keeps the no-op tile URL and its ETag identical to before.
        if (
            self.spacing_min_ft is not None
            or self.spacing_max_ft is not None
            or not self.spacing_include_no_neighbor
            or not self.spacing_include_no_data
        ):
            col = Well.lateral_closer_xy_ft
            measured_parts: list[ColumnElement[bool]] = [
                col.isnot(None),
                col != SPACING_SENTINEL_FT,
            ]
            if self.spacing_min_ft is not None:
                measured_parts.append(col >= self.spacing_min_ft)
            if self.spacing_max_ft is not None:
                measured_parts.append(col <= self.spacing_max_ft)
            admitted: list[ColumnElement[bool]] = [and_(*measured_parts)]
            if self.spacing_include_no_neighbor:
                admitted.append(col == SPACING_SENTINEL_FT)
            if self.spacing_include_no_data:
                admitted.append(col.is_(None))
            clauses.append(admitted[0] if len(admitted) == 1 else or_(*admitted))
        if self.well_name_contains:
            clauses.append(
                Well.name.ilike(f"%{escape_like(self.well_name_contains)}%", escape="\\")
            )
        if self.api10s:
            clauses.append(Well.api10.in_(self.api10s))
        # Water provenance: admit the selected classes; 'no_data' maps to
        # NULL. Empty tuple = no clause (all classes admitted).
        if self.water_sources:
            real = [v for v in self.water_sources if v != WATER_SOURCE_NO_DATA]
            admitted_ws: list[ColumnElement[bool]] = []
            if real:
                admitted_ws.append(Well.water_source.in_(real))
            if WATER_SOURCE_NO_DATA in self.water_sources:
                admitted_ws.append(Well.water_source.is_(None))
            clauses.append(admitted_ws[0] if len(admitted_ws) == 1 else or_(*admitted_ws))
        # Development scenario -- mirrored by tiles._build_filter_sql.
        if self.scenario_classes:
            real_sc = [v for v in self.scenario_classes if v != SCENARIO_NO_DATA]
            admitted_sc: list[ColumnElement[bool]] = []
            if real_sc:
                admitted_sc.append(Well.scenario_class.in_(real_sc))
            if SCENARIO_NO_DATA in self.scenario_classes:
                admitted_sc.append(Well.scenario_class.is_(None))
            clauses.append(admitted_sc[0] if len(admitted_sc) == 1 else or_(*admitted_sc))
        if self.scenario_benches:
            clauses.append(Well.scenario_bench.in_(self.scenario_benches))
        if self.parent_benches:
            clauses.append(self._parent_bench_clause())
        else:
            if self.parent_age_min_days is not None:
                clauses.append(Well.youngest_parent_age_days >= self.parent_age_min_days)
            if self.parent_age_max_days is not None:
                clauses.append(Well.oldest_parent_age_days <= self.parent_age_max_days)
        return clauses

    def _parent_bench_clause(self) -> ColumnElement[bool]:
        """EXISTS over jsonb_each(scenario_bench_context): a parent bench in
        the list, not the subject's own, with >= 1 parent inside the offset
        gate, optionally sided / dTVD-capped / age-bounded."""
        j = func.jsonb_each(Well.scenario_bench_context).table_valued("key", "value").alias("j")
        val = j.c.value

        def _field(key: str, typ: Any) -> ColumnElement[Any]:
            return cast(val.op("->>")(key), typ)

        dz = _field("parent_nearest_dtvd_ft", Numeric)
        conds: list[ColumnElement[bool]] = [
            j.c.key.in_(self.parent_benches),
            j.c.key.is_distinct_from(Well.scenario_bench),
            _field("n_parent", Integer) > 0,
            _field("parent_min_offset_ft", Numeric) <= PARENT_OFFSET_GATE_FT,
        ]
        if self.parent_side == "below":
            conds.append(dz > 0)
        elif self.parent_side == "above":
            conds.append(dz < 0)
        if self.parent_dtvd_max_ft is not None:
            conds.append(func.abs(dz) <= self.parent_dtvd_max_ft)
        if self.parent_age_min_days is not None:
            conds.append(_field("parent_min_age_days", Integer) >= self.parent_age_min_days)
        if self.parent_age_max_days is not None:
            conds.append(_field("parent_max_age_days", Integer) <= self.parent_age_max_days)
        return exists(select(1).select_from(j).where(*conds))


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
    formations: Annotated[str | None, Query(description="CSV of formation names")] = None,
    operators: Annotated[str | None, Query(description="CSV of operator names")] = None,
    counties: Annotated[str | None, Query()] = None,
    statuses: Annotated[str | None, Query(description="CSV of WellStatus codes")] = None,
    first_prod_start: Annotated[date | None, Query(alias="first_prod_start")] = None,
    first_prod_end: Annotated[date | None, Query(alias="first_prod_end")] = None,
    lateral_min_ft: Annotated[float | None, Query(ge=0)] = None,
    lateral_max_ft: Annotated[float | None, Query(ge=0)] = None,
    spacing_min_ft: Annotated[float | None, Query(ge=0)] = None,
    spacing_max_ft: Annotated[float | None, Query(ge=0)] = None,
    spacing_include_no_neighbor: Annotated[
        bool,
        Query(
            description=(
                "Include known-standalone wells (LateralCloserXY at the "
                "2800-ft no-neighbor cap). Live with or without a range."
            )
        ),
    ] = True,
    spacing_include_no_data: Annotated[
        bool,
        Query(
            description=(
                "Include wells with no spacing data (LateralCloserXY NULL "
                "— absent from Novi WellSpacing). Live with or without a range."
            )
        ),
    ] = True,
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
    water_sources: Annotated[
        str | None,
        Query(
            description=(
                "CSV of water-provenance classes to admit (measured, "
                "calculated, indeterminate, insufficient, no_data). "
                "Empty/absent = all classes (the flag-only default)."
            )
        ),
    ] = None,
    scenario_classes: Annotated[
        str | None,
        Query(
            description=(
                "CSV of development-scenario classes (sandwich, topfill, "
                "underfill, codev_stack, standalone, no_data). Empty = all."
            )
        ),
    ] = None,
    scenario_benches: Annotated[
        str | None, Query(description="CSV of TVD-corrected subject benches")
    ] = None,
    parent_benches: Annotated[
        str | None,
        Query(
            description=(
                "CSV of parent benches (bench-pair: a > 180 d older parent in "
                "that bench within the 660-ft lateral offset gate; no vertical "
                "window, no shielding)"
            )
        ),
    ] = None,
    parent_side: Annotated[
        str | None, Query(description="any | above | below (with parent_benches)")
    ] = None,
    parent_dtvd_max_ft: Annotated[float | None, Query(ge=0)] = None,
    parent_age_min_days: Annotated[int | None, Query(ge=0)] = None,
    parent_age_max_days: Annotated[int | None, Query(ge=0)] = None,
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
        spacing_include_no_neighbor=spacing_include_no_neighbor,
        spacing_include_no_data=spacing_include_no_data,
        well_name_contains=(well_name_contains or "").strip() or None,
        api10s=tuple(_split_csv(api10s)),
        # Unknown class names are dropped silently (same tolerance as
        # statuses) so a future class addition can't 400 an old client.
        water_sources=tuple(
            v
            for v in _split_csv(water_sources)
            if v in WATER_SOURCE_VALUES or v == WATER_SOURCE_NO_DATA
        ),
        scenario_classes=tuple(
            v
            for v in _split_csv(scenario_classes)
            if v in SCENARIO_CLASS_VALUES or v == SCENARIO_NO_DATA
        ),
        scenario_benches=tuple(_split_csv(scenario_benches)),
        parent_benches=tuple(_split_csv(parent_benches)),
        parent_side=parent_side if parent_side in PARENT_SIDES else "any",
        parent_dtvd_max_ft=parent_dtvd_max_ft,
        parent_age_min_days=parent_age_min_days,
        parent_age_max_days=parent_age_max_days,
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
        "spacing_include_no_neighbor": spec.spacing_include_no_neighbor,
        "spacing_include_no_data": spec.spacing_include_no_data,
        "well_name_contains": spec.well_name_contains,
        "api10s": list(spec.api10s),
        "water_sources": list(spec.water_sources),
        "scenario_classes": list(spec.scenario_classes),
        "scenario_benches": list(spec.scenario_benches),
        "parent_benches": list(spec.parent_benches),
        "parent_side": spec.parent_side,
        "parent_dtvd_max_ft": spec.parent_dtvd_max_ft,
        "parent_age_min_days": spec.parent_age_min_days,
        "parent_age_max_days": spec.parent_age_max_days,
    }
