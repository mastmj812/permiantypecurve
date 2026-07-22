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


_FETCH_SQL = (
    text(
        """
        SELECT deal_id, scenario_id, well_name, formation,
               completed_lateral_ft, drilled_lateral_ft, well_type,
               detail->>'category' AS category,
               NULLIF(detail->>'pdp_count_3mi', '')::int AS pdp_count_3mi,
               detail->>'handoff_category' AS handoff_category
        FROM narvi.inventory_well
        WHERE (deal_id, scenario_id) IN :pairs
        ORDER BY deal_id, scenario_id, well_name
        """
    ).bindparams(bindparam("pairs", expanding=True))
)


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


_UPDATED_AT_SQL = (
    text(
        """
        SELECT deal_id, scenario_id, updated_at
        FROM narvi.scenario
        WHERE (deal_id, scenario_id) IN :pairs
        """
    ).bindparams(bindparam("pairs", expanding=True))
)


def fetch_scenario_updated_at(
    wh: Session, selections: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], str]:
    """updated_at (ISO) per (deal_id, scenario_id) — the staleness probe
    for pinned blueox_config selections. Missing scenarios are simply
    absent from the result."""
    if not selections:
        return {}
    rows = wh.execute(
        _UPDATED_AT_SQL, {"pairs": [tuple(p) for p in selections]}
    ).all()
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
    rows = wh.execute(
        _DETAIL_WELLS_SQL, {"deal_id": deal_id, "scenario_id": scenario_id}
    ).all()
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
                    float(r.completed_lateral_ft)
                    if r.completed_lateral_ft is not None
                    else None
                ),
                target_tvd_ft=(
                    float(r.target_tvd_ft) if r.target_tvd_ft is not None else None
                ),
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
    return [
        NarviInventoryWell(
            deal_id=r.deal_id,
            scenario_id=r.scenario_id,
            well_name=r.well_name,
            formation=r.formation,
            completed_lateral_ft=(
                float(r.completed_lateral_ft)
                if r.completed_lateral_ft is not None
                else None
            ),
            drilled_lateral_ft=(
                float(r.drilled_lateral_ft)
                if r.drilled_lateral_ft is not None
                else None
            ),
            well_type=r.well_type,
            category=r.category,
            pdp_count_3mi=r.pdp_count_3mi,
            handoff_category=r.handoff_category,
        )
        for r in rows
    ]
