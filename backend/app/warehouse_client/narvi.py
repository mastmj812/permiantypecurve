"""Fetch narvi-planned inventory wells from ``narvi.inventory_well``.

narvi is the inventory-planning cockpit: the engineer generates (or
curates from novi_intel) the planned locations per DSU and persists
them to the app-owned ``narvi.*`` schema in the oilgas warehouse. The
Blue Ox curve-drop's ``inventory`` sheet is populated from those rows —
narvi remains the single writer; this module only reads.

Column mapping to the Blue Ox contract (§1.3):

* ``completed_lateral_ft`` (narvi's EUR driver — sum of producing legs)
  -> ``producing_lateral_ft`` (drives volume scaling)
* ``drilled_lateral_ft`` (narvi's D&C driver — legs + turn arc)
  -> ``drilled_lateral_ft`` (drives per-ft capex)
* ``formation`` is a ``formation_blueox`` bench code; the export spec
  maps benches -> zones (a zone commonly spans benches, e.g. WCA_1 +
  WCA_2 -> one WCA zone).

A drop may merge several narvi scenarios (deal_id, scenario_id) pairs:
DSUs are planned as separate scenarios, and geometry constraints can
force a split (e.g. U-turn wells in half a DSU saved as their own
scenario). Selection is always explicit — narvi deal_ids are free text
and do not correspond to anduin deal names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

# Handoff (reserve-class) vocabulary shared with the Blue Ox contract:
# PDP = existing producer, PUD = >=3 in-bench PDP offsets within 3 mi,
# UPSIDE = everything else. narvi persists the resolved value per well
# (user-overridable there); the derivation below is the agreed fallback.
HANDOFF_CATEGORIES: tuple[str, ...] = ("PDP", "PUD", "UPSIDE")


def derive_handoff_category(
    handoff_category: str | None, category: str | None, pud_support: bool
) -> str:
    """Resolve one well's PDP / PUD / UPSIDE class.

    narvi's persisted ``handoff_category`` WINS when present and valid;
    otherwise: existing producers -> PDP; ``pud_support`` (pdp_count_3mi
    >= 3) -> PUD; unscored or under-supported -> UPSIDE. Single source
    of truth for this rule on the anduin side — the export assembly and
    the scenario pick-list breakdown must never disagree.
    """
    if handoff_category:
        cat = handoff_category.strip().upper()
        if cat in HANDOFF_CATEGORIES:
            return cat
    if (category or "").lower() == "pdp":
        return "PDP"
    if pud_support:
        return "PUD"
    return "UPSIDE"


@dataclass(frozen=True)
class NarviInventoryWell:
    deal_id: str
    scenario_id: str
    well_name: str
    formation: str | None  # formation_blueox bench code
    completed_lateral_ft: float | None
    drilled_lateral_ft: float | None
    well_type: str
    # narvi's well category: 'generated' (narvi-planned) / 'pud' / 'res'
    # (curated from novi_intel) are plannable inventory; 'pdp' is an
    # EXISTING producer (its well_name is the api10). PDP wells ride
    # along on the handoff inventory tab (category PDP) so downstream
    # gunbarrel automation sees the whole DSU stack — but they are
    # never counted as locations.
    category: str | None = None
    # Offset-PDP support count within 3 mi (curated.intel_pdp_support
    # convention). Drives the handoff category: >=3 -> PUD, <=2/null ->
    # UPSIDE. NULL until narvi evaluates it for a stick.
    pdp_count_3mi: int | None = None
    # narvi's user-facing category (auto from pdp_count_3mi, user-
    # overridable in the narvi UI, like the TVD override). When
    # present it WINS over the derivation here. NULL until the narvi
    # feature lands.
    handoff_category: str | None = None
    # Gunbarrel geometry (the inventory sheet's geometry columns —
    # 2026-07-27 amendment): landing TVD, lateral azimuth, per-leg
    # cross-section offsets (narvi's gunbarrel_x_ft, one per producing
    # leg) and per-leg heel/toe WGS84 endpoints as (heel_lon, heel_lat,
    # toe_lon, toe_lat). Empty/None for rows saved before narvi
    # persisted them or for manually specified inventory.
    target_tvd_ft: float | None = None
    lateral_azimuth_deg: float | None = None
    gunbarrel_xs: tuple[float, ...] = ()
    legs_lonlat: tuple[tuple[float, float, float, float], ...] = ()
    # Representative novi_intel set persisted by narvi at scenario save
    # (detail->novi_rep: mode 'self' | 'neighborhood', stick_ids, n,
    # low_n, intel_vintage, ...). None on legacy saves — the export
    # assembly falls back to the same warehouse rule
    # (curated.intel_representative_sticks) on the fly.
    novi_rep: dict[str, Any] | None = None
    # Novi unique_id for pud/res pass-through wells (the intel_locations
    # / intel_forecast join key); None for generated wells and PDP.
    novi_wellname: str | None = None


_FETCH_SQL = text(
    """
        SELECT deal_id, scenario_id, well_name, formation,
               completed_lateral_ft, drilled_lateral_ft, well_type,
               target_tvd_ft, lateral_azimuth_deg,
               detail->>'category' AS category,
               NULLIF(detail->>'pdp_count_3mi', '')::int AS pdp_count_3mi,
               detail->>'handoff_category' AS handoff_category,
               detail->'legs' AS legs_detail,
               detail->'novi_rep' AS novi_rep,
               detail->>'novi_wellname' AS novi_wellname
        FROM narvi.inventory_well
        WHERE (deal_id, scenario_id) IN :pairs
        ORDER BY deal_id, scenario_id, well_name
        """
).bindparams(bindparam("pairs", expanding=True))


def _parse_legs(
    legs_detail: list[Any] | None,
) -> tuple[tuple[float, ...], tuple[tuple[float, float, float, float], ...]]:
    """(gunbarrel offsets, heel/toe lonlat quads) from a detail->'legs'
    JSON array. Tolerates missing/odd shapes (legacy rows) by simply
    omitting the value — callers treat empty as "not persisted"."""
    xs: list[float] = []
    quads: list[tuple[float, float, float, float]] = []
    for leg in legs_detail or []:
        if not isinstance(leg, dict):
            continue
        x = leg.get("gunbarrel_x_ft")
        if isinstance(x, (int, float)):
            xs.append(float(x))
        heel = leg.get("heel_lonlat")
        toe = leg.get("toe_lonlat")
        if (
            isinstance(heel, (list, tuple))
            and len(heel) == 2
            and isinstance(toe, (list, tuple))
            and len(toe) == 2
        ):
            quads.append((float(heel[0]), float(heel[1]), float(toe[0]), float(toe[1])))
    return tuple(xs), tuple(quads)


@dataclass(frozen=True)
class NarviBenchCount:
    """Well count for one (bench, handoff class) cell of a scenario —
    lets the config UI show what a scenario actually contains before
    the engineer maps benches into zones."""

    formation: str | None  # formation_blueox bench code; None = unset
    category: str  # PDP / PUD / UPSIDE
    n: int


@dataclass(frozen=True)
class NarviScenario:
    """One saved narvi scenario (header row) — the config UI's pick list."""

    deal_id: str
    scenario_id: str
    name: str | None
    well_type: str
    total_wells: int | None
    total_completed_ft: float | None
    updated_at: str  # ISO 8601; pinned into blueox_config for staleness checks
    breakdown: tuple[NarviBenchCount, ...] = field(default=())


_SCENARIOS_SQL = text(
    """
    SELECT deal_id, scenario_id, name, well_type, total_wells,
           total_completed_ft, updated_at
    FROM narvi.scenario
    ORDER BY updated_at DESC
    """
)


# Aggregated per (scenario, bench, raw classification) — the handoff
# class itself is resolved in Python (derive_handoff_category) so the
# rule lives in exactly one place.
_BREAKDOWN_SQL = text(
    """
    SELECT deal_id, scenario_id, formation,
           detail->>'handoff_category' AS handoff_category,
           detail->>'category' AS category,
           COALESCE(NULLIF(detail->>'pdp_count_3mi', '')::int >= 3, FALSE)
               AS pud_support,
           COUNT(*)::int AS n
    FROM narvi.inventory_well
    GROUP BY 1, 2, 3, 4, 5, 6
    """
)


def fetch_narvi_scenarios(wh: Session) -> list[NarviScenario]:
    """All saved narvi scenarios, newest first, each with its per-bench
    well-count breakdown by handoff class."""
    rows = wh.execute(_SCENARIOS_SQL).all()
    counts: dict[tuple[str, str], dict[tuple[str | None, str], int]] = {}
    for b in wh.execute(_BREAKDOWN_SQL).all():
        cat = derive_handoff_category(b.handoff_category, b.category, b.pud_support)
        cell = counts.setdefault((b.deal_id, b.scenario_id), {})
        cell[(b.formation, cat)] = cell.get((b.formation, cat), 0) + b.n
    return [
        NarviScenario(
            deal_id=r.deal_id,
            scenario_id=r.scenario_id,
            name=r.name,
            well_type=r.well_type,
            total_wells=r.total_wells,
            total_completed_ft=(
                float(r.total_completed_ft) if r.total_completed_ft is not None else None
            ),
            updated_at=r.updated_at.isoformat(),
            breakdown=tuple(
                NarviBenchCount(formation=f, category=c, n=n)
                for (f, c), n in sorted(
                    counts.get((r.deal_id, r.scenario_id), {}).items(),
                    key=lambda kv: (
                        HANDOFF_CATEGORIES.index(kv[0][1]),
                        kv[0][0] or "",
                    ),
                )
            ),
        )
        for r in rows
    ]


_UPDATED_AT_SQL = text(
    """
        SELECT deal_id, scenario_id, updated_at
        FROM narvi.scenario
        WHERE (deal_id, scenario_id) IN :pairs
        """
).bindparams(bindparam("pairs", expanding=True))


def fetch_scenario_updated_at(
    wh: Session, selections: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """updated_at (ISO) per (deal_id, scenario_id) — the staleness probe
    for pinned blueox_config selections. Missing scenarios are simply
    absent from the result."""
    if not selections:
        return {}
    rows = wh.execute(_UPDATED_AT_SQL, {"pairs": [tuple(p) for p in selections]}).all()
    return {(r.deal_id, r.scenario_id): r.updated_at.isoformat() for r in rows}


@dataclass(frozen=True)
class NarviScenarioWellGeo:
    """One scenario well with everything the dossier's map + gunbarrel
    need: WGS84 leg/turn geometry for the plan view, cross-section
    offset + TVD for the gunbarrel."""

    well_name: str
    formation: str | None  # formation_blueox bench code
    category: str  # resolved handoff class: PDP / PUD / UPSIDE
    provenance: str | None  # narvi source: generated / pud / res / pdp
    well_type: str  # single / uturn
    n_legs: int
    completed_lateral_ft: float | None
    target_tvd_ft: float | None
    legs_geojson: str | None  # MultiLineString, WGS84
    turn_geojson: str | None  # LineString (U-turn arc), WGS84
    # Perpendicular offsets (ft) of each leg along the cross-section
    # axis — narvi's gunbarrel_x_ft, one per leg.
    gunbarrel_xs: tuple[float, ...]


@dataclass(frozen=True)
class NarviScenarioDetail:
    deal_id: str
    scenario_id: str
    name: str | None
    well_type: str
    aoi_geojson: str | None  # parcel Polygon/MultiPolygon, WGS84
    wells: tuple[NarviScenarioWellGeo, ...]


_DETAIL_HEADER_SQL = text(
    """
    SELECT name, well_type, ST_AsGeoJSON(aoi_geom) AS aoi_geojson
    FROM narvi.scenario
    WHERE deal_id = :deal_id AND scenario_id = :scenario_id
    """
)

_DETAIL_WELLS_SQL = text(
    """
    SELECT well_name, formation, well_type, n_legs,
           completed_lateral_ft, target_tvd_ft,
           ST_AsGeoJSON(legs_geom) AS legs_geojson,
           ST_AsGeoJSON(turn_geom) AS turn_geojson,
           detail->>'category' AS category,
           NULLIF(detail->>'pdp_count_3mi', '')::int AS pdp_count_3mi,
           detail->>'handoff_category' AS handoff_category,
           detail->'legs' AS legs_detail
    FROM narvi.inventory_well
    WHERE deal_id = :deal_id AND scenario_id = :scenario_id
    ORDER BY well_name
    """
)


def fetch_narvi_scenario_detail(
    wh: Session, deal_id: str, scenario_id: str
) -> NarviScenarioDetail | None:
    """Full geometry payload for one scenario (dossier map + gunbarrel).

    Returns None when the scenario doesn't exist. PDP producers ride
    along (category PDP) so the views show the whole DSU stack.
    """
    header = wh.execute(
        _DETAIL_HEADER_SQL, {"deal_id": deal_id, "scenario_id": scenario_id}
    ).one_or_none()
    if header is None:
        return None
    rows = wh.execute(_DETAIL_WELLS_SQL, {"deal_id": deal_id, "scenario_id": scenario_id}).all()
    wells: list[NarviScenarioWellGeo] = []
    for r in rows:
        xs: list[float] = []
        for leg in r.legs_detail or []:
            x = leg.get("gunbarrel_x_ft") if isinstance(leg, dict) else None
            if isinstance(x, (int, float)):
                xs.append(float(x))
        wells.append(
            NarviScenarioWellGeo(
                well_name=r.well_name,
                formation=r.formation,
                category=derive_handoff_category(
                    r.handoff_category,
                    r.category,
                    r.pdp_count_3mi is not None and r.pdp_count_3mi >= 3,
                ),
                provenance=r.category,
                well_type=r.well_type,
                n_legs=r.n_legs,
                completed_lateral_ft=(
                    float(r.completed_lateral_ft) if r.completed_lateral_ft is not None else None
                ),
                target_tvd_ft=(float(r.target_tvd_ft) if r.target_tvd_ft is not None else None),
                legs_geojson=r.legs_geojson,
                turn_geojson=r.turn_geojson,
                gunbarrel_xs=tuple(xs),
            )
        )
    return NarviScenarioDetail(
        deal_id=deal_id,
        scenario_id=scenario_id,
        name=header.name,
        well_type=header.well_type,
        aoi_geojson=header.aoi_geojson,
        wells=tuple(wells),
    )


@dataclass(frozen=True)
class NarviDealStick:
    """One planned inventory well of a narvi deal — the main map's
    stick-overlay grain. PDP producers are excluded before this level:
    the map already renders them from anduin's own wells layers."""

    deal_id: str
    scenario_id: str
    scenario_name: str | None
    well_name: str
    formation: str | None  # RAW formation_blueox bench code, may carry _b
    category: str  # resolved handoff class: PUD / UPSIDE
    well_type: str  # single / uturn
    legs_geojson: str | None  # MultiLineString, WGS84
    turn_geojson: str | None  # LineString (U-turn arc), WGS84


@dataclass(frozen=True)
class NarviDealSticksResult:
    """Sticks for a multi-deal selection. narvi deal_ids are per-DSU in
    practice (one engineer-named deal spans many deal_id rows, e.g.
    vault_dsu_*), so the overlay selects a SET of deal_ids and the
    per-id resolution is reported honestly: a typo'd id lands in
    missing_deal_ids instead of silently shrinking the stick count."""

    found_deal_ids: tuple[str, ...]
    missing_deal_ids: tuple[str, ...]
    sticks: tuple[NarviDealStick, ...]


_DEAL_EXISTS_SQL = text(
    """
    SELECT DISTINCT deal_id FROM narvi.scenario WHERE deal_id IN :deal_ids
    """
).bindparams(bindparam("deal_ids", expanding=True))

_DEAL_STICKS_SQL = text(
    """
    SELECT w.deal_id, w.scenario_id, s.name AS scenario_name, w.well_name,
           w.formation, w.well_type,
           ST_AsGeoJSON(w.legs_geom) AS legs_geojson,
           ST_AsGeoJSON(w.turn_geom) AS turn_geojson,
           detail->>'category' AS category,
           NULLIF(detail->>'pdp_count_3mi', '')::int AS pdp_count_3mi,
           detail->>'handoff_category' AS handoff_category
    FROM narvi.inventory_well w
    JOIN narvi.scenario s
      ON s.deal_id = w.deal_id AND s.scenario_id = w.scenario_id
    WHERE w.deal_id IN :deal_ids
    ORDER BY w.deal_id, w.scenario_id, w.well_name
    """
).bindparams(bindparam("deal_ids", expanding=True))


def fetch_narvi_deal_sticks(
    wh: Session, deal_ids: Sequence[str]
) -> NarviDealSticksResult:
    """All planned (non-PDP) inventory sticks across every scenario of
    the selected narvi deals — the main map's dashed-stick overlay.

    An empty selection short-circuits to an empty result. Ids matching
    no scenario are reported in missing_deal_ids; a found deal whose
    wells all resolved to PDP simply contributes no sticks.
    """
    requested = list(dict.fromkeys(deal_ids))  # de-dupe, keep order
    if not requested:
        return NarviDealSticksResult((), (), ())
    found = {
        r.deal_id for r in wh.execute(_DEAL_EXISTS_SQL, {"deal_ids": requested}).all()
    }
    missing = tuple(d for d in requested if d not in found)
    if not found:
        return NarviDealSticksResult((), missing, ())
    rows = wh.execute(_DEAL_STICKS_SQL, {"deal_ids": requested}).all()
    sticks: list[NarviDealStick] = []
    for r in rows:
        cat = derive_handoff_category(
            r.handoff_category,
            r.category,
            r.pdp_count_3mi is not None and r.pdp_count_3mi >= 3,
        )
        if cat == "PDP":
            continue
        sticks.append(
            NarviDealStick(
                deal_id=r.deal_id,
                scenario_id=r.scenario_id,
                scenario_name=r.scenario_name,
                well_name=r.well_name,
                formation=r.formation,
                category=cat,
                well_type=r.well_type,
                legs_geojson=r.legs_geojson,
                turn_geojson=r.turn_geojson,
            )
        )
    return NarviDealSticksResult(
        found_deal_ids=tuple(d for d in requested if d in found),
        missing_deal_ids=missing,
        sticks=tuple(sticks),
    )


@dataclass(frozen=True)
class NarviDsuFrame:
    """The gunbarrel projection frame of one DSU/scenario — everything
    needed to reproduce narvi's cross-section offsets externally:
    offset = signed projection of a leg midpoint onto the axis 90°
    clockwise of azimuth_deg (folded to [0°, 180°)) through the origin
    (the parcel centroid), in feet. Feeds the workbook's dsu_meta sheet
    (2026-07-27 amendment)."""

    dsu_id: str  # "<narvi deal_id>/<scenario_id>"
    azimuth_deg: float | None
    origin_lon: float | None
    origin_lat: float | None


# Origin must match narvi exactly: narvi takes the parcel centroid in
# its WORK CRS (UTM 13N / EPSG:32613), so centroid there, then back to
# WGS84 — a 4326 centroid can differ by metres on stretched parcels.
_DSU_FRAME_SQL = text(
    """
        SELECT deal_id, scenario_id, azimuth_deg,
               ST_X(ST_Transform(
                   ST_Centroid(ST_Transform(aoi_geom, 32613)), 4326)) AS origin_lon,
               ST_Y(ST_Transform(
                   ST_Centroid(ST_Transform(aoi_geom, 32613)), 4326)) AS origin_lat
        FROM narvi.scenario
        WHERE (deal_id, scenario_id) IN :pairs
        ORDER BY deal_id, scenario_id
        """
).bindparams(bindparam("pairs", expanding=True))


def fetch_narvi_dsu_frames(
    wh: Session, selections: Sequence[tuple[str, str]]
) -> list[NarviDsuFrame]:
    """Gunbarrel projection frames for the selected scenarios (missing
    scenarios are simply absent — the caller's zero-rows check on the
    inventory fetch already flags typos)."""
    if not selections:
        return []
    rows = wh.execute(_DSU_FRAME_SQL, {"pairs": [tuple(p) for p in selections]}).all()
    return [
        NarviDsuFrame(
            dsu_id=f"{r.deal_id}/{r.scenario_id}",
            azimuth_deg=(float(r.azimuth_deg) if r.azimuth_deg is not None else None),
            origin_lon=(float(r.origin_lon) if r.origin_lon is not None else None),
            origin_lat=(float(r.origin_lat) if r.origin_lat is not None else None),
        )
        for r in rows
    ]


def fetch_narvi_inventory(
    wh: Session, selections: Sequence[tuple[str, str]]
) -> list[NarviInventoryWell]:
    """Inventory wells for the selected (deal_id, scenario_id) pairs.

    Returns [] for an empty selection. Callers are responsible for
    noticing selections that matched zero rows (a typo'd scenario id
    must not silently shrink the location count Blue Ox values).
    """
    if not selections:
        return []
    rows = wh.execute(_FETCH_SQL, {"pairs": [tuple(p) for p in selections]}).all()
    out: list[NarviInventoryWell] = []
    for r in rows:
        xs, quads = _parse_legs(r.legs_detail)
        out.append(
            NarviInventoryWell(
                deal_id=r.deal_id,
                scenario_id=r.scenario_id,
                well_name=r.well_name,
                formation=r.formation,
                completed_lateral_ft=(
                    float(r.completed_lateral_ft) if r.completed_lateral_ft is not None else None
                ),
                drilled_lateral_ft=(
                    float(r.drilled_lateral_ft) if r.drilled_lateral_ft is not None else None
                ),
                well_type=r.well_type,
                category=r.category,
                pdp_count_3mi=r.pdp_count_3mi,
                handoff_category=r.handoff_category,
                target_tvd_ft=(float(r.target_tvd_ft) if r.target_tvd_ft is not None else None),
                lateral_azimuth_deg=(
                    float(r.lateral_azimuth_deg) if r.lateral_azimuth_deg is not None else None
                ),
                gunbarrel_xs=xs,
                legs_lonlat=quads,
                novi_rep=(r.novi_rep if isinstance(r.novi_rep, dict) else None),
                novi_wellname=r.novi_wellname,
            )
        )
    return out
