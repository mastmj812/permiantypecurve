"""Type-well build-up: derive the cull waterfall from stored provenance.

The partners' ask, verbatim: "a list of wells you started with by
formation, then which ones you culled on vintage, geo, or any other
reason to get to the final set." This module answers it as a pure
computation over one saved TypeCurve row — no DB, no HTTP — so the
xlsx sheet, the CSV, and any future UI panel all read one truth.

Design invariants:

  * The waterfall is COMPUTED here, never stored. ``provenance`` holds
    raw inputs (AOI, universe snapshot, filter snapshot, partition,
    coded exclusions, post-save ledger); this module attributes each
    universe well to exactly ONE disposition — the first stage that
    removes it — in the fixed order below.
  * Selection events are supporting narrative only. The waterfall works
    from the universe snapshot + filter snapshot + final membership,
    so an incomplete event log (dropped-oldest cap, click-built
    cohorts) degrades the story, never the numbers.
  * Empty provenance (curve saved before migration 0026, or an
    old-workflow save) → ``degraded=True`` and an included-only roster.
    Honest "not recorded", never a fabricated history.

Stage order (each universe well takes the FIRST stage that removes it):

  1. vintage          first prod outside [first_prod_start, first_prod_end]
  2. lateral          lateral_ft outside [lateral_min_ft, lateral_max_ft]
  3. spacing          same-zone spacing (LateralCloserXY) outside range,
                      or unbounded (NULL / 2800-sentinel) while the
                      include-unbounded toggle was off
  4. filters_other    status / operator / explicit api10 allow-list
  5. not_selected     survived the filters but never entered the cohort
  6. no_peak          forecast batch found no production peak
  7. short_history    < cutoff months post-peak AND not cohort-transferred
  8. review_excluded  engineer un-ticked on Review (code + note)
  9. post_save_removed removed from the saved curve later (code + note)
  → included          the final type-curve cohort

Wells in ``included_api10s`` but OUTSIDE the universe (add-wells flow,
pasted lists, or membership drift on inherited-provenance versions) are
reported separately — the reconciliation row accounts for them
explicitly rather than pretending they came through the funnel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.models import TypeCurve
from app.type_curves.reason_codes import REVIEW_REASON_CODES
from app.wells_api.filters import SPACING_SENTINEL_FT

# Stage key → sheet description. Order IS the waterfall order.
STAGE_DESCRIPTIONS: list[tuple[str, str]] = [
    ("vintage", "First prod outside vintage window"),
    ("lateral", "Lateral length outside range"),
    ("spacing", "Same-zone spacing outside range / unbounded"),
    ("filters_other", "Status / operator / list filters"),
    ("not_selected", "Not carried into cohort (geo / manual)"),
    ("no_peak", "No production peak found"),
    ("short_history", "Short post-peak history (not transferred)"),
    ("review_excluded", "Engineering review exclusion"),
    ("post_save_removed", "Removed after save"),
    ("unaccounted", "In funnel but absent from final membership"),
]

TRANSFERRED_ANNOTATION = "included (cohort-transferred params)"


@dataclass(frozen=True)
class BuildupRow:
    """One universe well with its resolved disposition."""

    api10: str
    name: str | None
    operator: str | None
    formation: str | None
    first_prod_date: str | None
    lateral_ft: float | None
    status: str | None
    disposition: str  # stage key or "included"
    reason_code: str | None  # user code for review/post-save stages
    note: str | None
    annotation: str | None = None  # e.g. cohort-transferred marker
    # Same-zone spacing (verbatim snapshot value; 2800.0 = no-neighbor
    # sentinel, None = absent from WellSpacing).
    lateral_closer_xy_ft: float | None = None


@dataclass(frozen=True)
class WaterfallRow:
    stage: str
    description: str
    culled: int
    remaining: int


@dataclass(frozen=True)
class BuildupHeader:
    formations: list[str]
    aoi_polygon_count: int
    aoi_total_area_sq_mi: float | None
    universe_computed_at: str | None
    universe_count: int
    universe_truncated: bool
    # Humanized (label, value) criteria lines from the filter snapshot.
    criteria: list[tuple[str, str]]
    cutoff_months: int | None
    narrative_truncated: bool


@dataclass(frozen=True)
class Buildup:
    degraded: bool
    header: BuildupHeader | None
    waterfall: list[WaterfallRow]
    rows: list[BuildupRow]
    out_of_universe_included: list[str]
    included_count: int
    # remaining-after-last-stage + out-of-universe == included_count
    reconciles: bool = True
    notes: list[str] = field(default_factory=list)


def _criteria_lines(fs: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    start, end = fs.get("first_prod_start"), fs.get("first_prod_end")
    if start and end:
        out.append(("vintage_criterion", f"first prod {start} to {end}"))
    elif start:
        out.append(("vintage_criterion", f"first prod >= {start}"))
    elif end:
        out.append(("vintage_criterion", f"first prod <= {end}"))
    lat_min, lat_max = fs.get("lateral_min_ft"), fs.get("lateral_max_ft")
    if lat_min is not None and lat_max is not None:
        out.append(("lateral_criterion", f"{lat_min:,.0f} - {lat_max:,.0f} ft"))
    elif lat_min is not None:
        out.append(("lateral_criterion", f">= {lat_min:,.0f} ft"))
    elif lat_max is not None:
        out.append(("lateral_criterion", f"<= {lat_max:,.0f} ft"))
    sp_min, sp_max = fs.get("spacing_min_ft"), fs.get("spacing_max_ft")
    if sp_min is not None or sp_max is not None:
        if sp_min is not None and sp_max is not None:
            rng = f"{sp_min:,.0f} - {sp_max:,.0f} ft"
        elif sp_min is not None:
            rng = f">= {sp_min:,.0f} ft"
        else:
            rng = f"<= {sp_max:,.0f} ft"
        unbounded = (
            "no-neighbor wells included"
            if fs.get("spacing_include_unbounded")
            else "no-neighbor wells excluded"
        )
        out.append(
            (
                "spacing_criterion",
                f"{rng} same-zone offset at first prod ({unbounded})",
            )
        )
    statuses = fs.get("statuses") or []
    if statuses:
        out.append(("status_criterion", ", ".join(str(s) for s in statuses)))
    operators = fs.get("operators") or []
    if operators:
        out.append(("operator_criterion", ", ".join(str(o) for o in operators)))
    name_sub = fs.get("well_name_contains")
    if name_sub:
        out.append(("well_name_criterion", f"name contains '{name_sub}'"))
    api10s = fs.get("api10s") or []
    if api10s:
        out.append(("api10_allowlist", f"{len(api10s)} explicit api10s"))
    return out


def _replay_cohort(events: list[dict[str, Any]]) -> set[str]:
    """Approximate final cohort membership from the event narrative."""
    cohort: set[str] = set()
    for ev in events:
        kind = ev.get("kind")
        api10s = ev.get("api10s") or []
        if kind in ("polygon", "bbox", "click_add"):
            cohort.update(api10s)
        elif kind in ("click_remove", "manual_remove"):
            cohort.difference_update(api10s)
    return cohort


def _outside(
    value: str | float | None,
    lo: Any,
    hi: Any,
) -> bool:
    """True when a bound is set and the value misses it. A None value
    with any bound set counts as outside — no date/length means the
    well can't demonstrate it met the criterion."""
    if lo is None and hi is None:
        return False
    if value is None:
        return True
    if lo is not None and value < lo:
        return True
    return hi is not None and value > hi


def compute_buildup(tc: TypeCurve) -> Buildup:
    included_list = list(tc.included_api10s or [])
    included = set(included_list)
    prov = dict(tc.provenance or {})

    # ---- degraded: nothing recorded --------------------------------
    if not prov:
        return Buildup(
            degraded=True,
            header=None,
            waterfall=[],
            rows=[
                BuildupRow(
                    api10=a, name=None, operator=None, formation=None,
                    first_prod_date=None, lateral_ft=None, status=None,
                    disposition="included", reason_code=None, note=None,
                )
                for a in included_list
            ],
            out_of_universe_included=[],
            included_count=len(included_list),
            notes=[
                "provenance not recorded — curve saved before build-up "
                "tracking (2026-08); roster is final membership only"
            ],
        )

    fs: dict[str, Any] = prov.get("filter_snapshot") or {}
    universe: dict[str, Any] = prov.get("universe") or {}
    uni_wells: list[dict[str, Any]] = universe.get("wells") or []
    partition: dict[str, Any] = prov.get("partition") or {}
    exclusions: dict[str, Any] = prov.get("exclusions") or {}
    removals: list[dict[str, Any]] = prov.get("post_save_removals") or []
    events: list[dict[str, Any]] = prov.get("selection_events") or []
    aoi_polygons: list[dict[str, Any]] = (prov.get("aoi") or {}).get(
        "polygons"
    ) or []

    short = set(partition.get("short") or [])
    no_peak = set(partition.get("no_peak") or [])
    removed_by: dict[str, dict[str, Any]] = {
        str(r.get("api10")): r for r in removals
    }

    # Funnel membership: the replayed narrative plus everything we KNOW
    # entered it (forecast partition, exclusions, the final cohort).
    cohort = _replay_cohort(events)
    cohort |= included | short | no_peak | set(exclusions)

    areas = [p.get("area_sq_mi") for p in aoi_polygons]
    known_areas = [a for a in areas if isinstance(a, (int, float))]
    header = BuildupHeader(
        formations=[str(f) for f in (prov.get("formations") or [])],
        aoi_polygon_count=len(aoi_polygons),
        aoi_total_area_sq_mi=(
            round(sum(known_areas), 1) if known_areas else None
        ),
        universe_computed_at=universe.get("computed_at"),
        universe_count=int(universe.get("well_count") or 0),
        universe_truncated=bool(universe.get("truncated")),
        criteria=_criteria_lines(fs),
        cutoff_months=partition.get("cutoff_months"),
        narrative_truncated=any(
            bool(ev.get("truncated")) for ev in events
        ),
    )

    notes: list[str] = []
    if not uni_wells:
        # Recorded provenance but no universe (click-only cohort, no
        # AOI drawn): the funnel can't be attributed — roster falls
        # back to final membership, criteria still stated.
        notes.append(
            "no AOI recorded (cohort built without a lasso/box draw) — "
            "starting universe unavailable; roster is final membership only"
        )
        fallback_rows = [
            BuildupRow(
                api10=a, name=None, operator=None, formation=None,
                first_prod_date=None, lateral_ft=None, status=None,
                disposition="included", reason_code=None, note=None,
            )
            for a in included_list
        ]
        return Buildup(
            degraded=False,
            header=header,
            waterfall=[],
            rows=fallback_rows,
            out_of_universe_included=[],
            included_count=len(included_list),
            notes=notes,
        )

    # ---- the waterfall ---------------------------------------------
    lo_date, hi_date = fs.get("first_prod_start"), fs.get("first_prod_end")
    lo_lat, hi_lat = fs.get("lateral_min_ft"), fs.get("lateral_max_ft")
    sp_min, sp_max = fs.get("spacing_min_ft"), fs.get("spacing_max_ft")
    sp_active = sp_min is not None or sp_max is not None
    sp_include_unbounded = bool(fs.get("spacing_include_unbounded"))
    statuses = {str(s) for s in (fs.get("statuses") or [])}
    operators = {str(o) for o in (fs.get("operators") or [])}
    name_sub = str(fs.get("well_name_contains") or "").casefold() or None
    allowlist = {str(a) for a in (fs.get("api10s") or [])}
    # counties exists in FilterSpec but has no UI and the universe
    # snapshot carries no county attr — folded into filters_other by
    # design decision; nothing to evaluate here.

    def _spacing_culled(w: dict[str, Any]) -> bool:
        # Mirrors FilterSpec.to_sqlalchemy_clauses: bounds bind only on
        # REAL spacing; NULL + the 2800 no-neighbor sentinel form the
        # "unbounded" class admitted solely by the include toggle.
        if not sp_active:
            return False
        v = w.get("lateral_closer_xy_ft")
        if v is None or v == SPACING_SENTINEL_FT:
            return not sp_include_unbounded
        return _outside(v, sp_min, sp_max)

    def disposition_for(w: dict[str, Any]) -> tuple[str, str | None, str | None]:
        api10 = str(w.get("api10"))
        if _outside(w.get("first_prod_date"), lo_date, hi_date):
            return "vintage", None, None
        if _outside(w.get("lateral_ft"), lo_lat, hi_lat):
            return "lateral", None, None
        if _spacing_culled(w):
            return "spacing", None, None
        if statuses and str(w.get("status")) not in statuses:
            return "filters_other", None, None
        if operators and str(w.get("operator")) not in operators:
            return "filters_other", None, None
        # Mirrors the ILIKE %sub% clause (case-insensitive contains).
        if name_sub and name_sub not in str(w.get("name") or "").casefold():
            return "filters_other", None, None
        if allowlist and api10 not in allowlist:
            return "filters_other", None, None
        if api10 not in cohort:
            return "not_selected", None, None
        if api10 in no_peak:
            return "no_peak", None, None
        if api10 in short and api10 not in included:
            return "short_history", None, None
        if api10 in exclusions:
            e = exclusions[api10]
            return "review_excluded", e.get("code"), e.get("note")
        if api10 in removed_by:
            r = removed_by[api10]
            return "post_save_removed", r.get("code"), r.get("note")
        if api10 in included:
            return "included", None, None
        # Survived every recorded stage yet isn't in the final set —
        # membership drift the record can't explain. Exposed, not hidden.
        return "unaccounted", None, None

    rows: list[BuildupRow] = []
    universe_api10s: set[str] = set()
    for w in uni_wells:
        api10 = str(w.get("api10"))
        universe_api10s.add(api10)
        stage, code, note = disposition_for(w)
        annotation = (
            TRANSFERRED_ANNOTATION
            if stage == "included" and api10 in short
            else None
        )
        rows.append(
            BuildupRow(
                api10=api10,
                name=w.get("name"),
                operator=w.get("operator"),
                formation=w.get("formation"),
                first_prod_date=w.get("first_prod_date"),
                lateral_ft=w.get("lateral_ft"),
                status=w.get("status"),
                disposition=stage,
                reason_code=code,
                note=note,
                annotation=annotation,
                lateral_closer_xy_ft=w.get("lateral_closer_xy_ft"),
            )
        )

    counts: dict[str, int] = {}
    for r in rows:
        counts[r.disposition] = counts.get(r.disposition, 0) + 1

    waterfall: list[WaterfallRow] = []
    remaining = len(rows)
    for stage, desc in STAGE_DESCRIPTIONS:
        culled = counts.get(stage, 0)
        if stage == "unaccounted" and culled == 0:
            continue  # only surfaces when real
        remaining -= culled
        waterfall.append(WaterfallRow(stage, desc, culled, remaining))

    out_of_universe = sorted(included - universe_api10s)
    reconciles = remaining + len(out_of_universe) == len(included_list)
    if not reconciles:
        notes.append(
            f"reconciliation gap: {remaining} in-universe survivors + "
            f"{len(out_of_universe)} added outside AOI != "
            f"{len(included_list)} final wells"
        )
    if header.universe_truncated:
        notes.append(
            "universe snapshot truncated at the sanity cap — counts "
            "understate the true formation+AOI universe"
        )
    if header.narrative_truncated:
        notes.append(
            "selection-event narrative truncated (event cap) — AOI and "
            "waterfall remain accurate; only the story of intermediate "
            "adds/removes is shortened"
        )

    return Buildup(
        degraded=False,
        header=header,
        waterfall=waterfall,
        rows=rows,
        out_of_universe_included=out_of_universe,
        included_count=len(included_list),
        reconciles=reconciles,
        notes=notes,
    )


def reason_label(code: str | None) -> str | None:
    """Human label for a reason code (roster + waterfall detail)."""
    if code is None:
        return None
    return REVIEW_REASON_CODES.get(code, code)
