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


_FETCH_SQL = (
    text(
        """
        SELECT deal_id, scenario_id, well_name, formation,
               completed_lateral_ft, drilled_lateral_ft, well_type
        FROM narvi.inventory_well
        WHERE (deal_id, scenario_id) IN :pairs
        ORDER BY deal_id, scenario_id, well_name
        """
    ).bindparams(bindparam("pairs", expanding=True))
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
        )
        for r in rows
    ]
