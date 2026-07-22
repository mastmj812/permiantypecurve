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
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


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
class NarviScenario:
    """One saved narvi scenario (header row) — the config UI's pick list."""

    deal_id: str
    scenario_id: str
    name: str | None
    well_type: str
    total_wells: int | None
    total_completed_ft: float | None
    updated_at: str  # ISO 8601; pinned into blueox_config for staleness checks


_SCENARIOS_SQL = text(
    """
    SELECT deal_id, scenario_id, name, well_type, total_wells,
           total_completed_ft, updated_at
    FROM narvi.scenario
    ORDER BY updated_at DESC
    """
)


def fetch_narvi_scenarios(wh: Session) -> list[NarviScenario]:
    """All saved narvi scenarios, newest first."""
    rows = wh.execute(_SCENARIOS_SQL).all()
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
