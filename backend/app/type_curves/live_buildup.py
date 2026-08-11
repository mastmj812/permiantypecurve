"""Live build-up preview: the cull waterfall over a DRAFT cohort.

The saved-curve waterfall (buildup.py) reads ``type_curves.provenance``.
During curation there is no saved row yet — the Map tab's Buildup drawer
wants the same attribution computed from the working state: the stamped
AOI polygons, the formation scope, the CURRENT FilterSpec, the cohort's
current membership, and any coded manual removals.

This module assembles those inputs into a synthetic provenance dict of
the exact stored shape and hands it to the one engine (compute_buildup)
via an in-memory TypeCurve stub — the same trick the compute-preview
path uses (_empty_tc_for_resolver). One engine, two read paths; the
drawer and the export sheet can never drift.

Draft semantics vs a saved curve:
  * selection_events is empty, so the replayed cohort == the membership
    list passed in. Filter stages, not_selected, and included resolve
    normally; no_peak / short_history / review_excluded / post_save_*
    never fire pre-forecast — honest for a draft.
  * The universe is computed fresh per call (deterministic given
    AOI + formations), never persisted here. The save path snapshots
    its own universe; a mid-session warehouse sync may shift the
    preview slightly — the saved snapshot is the record.

Pure assembly — no DB, no HTTP — so the DB-free suite exercises it with
a synthetic universe.
"""

from __future__ import annotations

from typing import Any

from app.db.models import TypeCurve
from app.type_curves.buildup import Buildup, compute_buildup

PROVENANCE_VERSION = 2


def draft_provenance(
    *,
    aoi_polygons: list[dict[str, Any]],
    formations: list[str],
    filter_snapshot: dict[str, Any],
    universe: dict[str, Any],
    manual_exclusions: dict[str, dict[str, Any]],
    polygon_areas_sq_mi: list[float | None] | None = None,
) -> dict[str, Any]:
    """Synthetic provenance of the stored (v2) shape for a draft cohort.

    ``universe`` is the compute_universe() result (or a synthetic one in
    tests). ``polygon_areas_sq_mi`` aligns with ``aoi_polygons`` when the
    caller computed geodesic areas (cosmetic header line); None entries
    render as unknown.
    """
    areas = polygon_areas_sq_mi or [None] * len(aoi_polygons)
    return {
        "version": PROVENANCE_VERSION,
        "recorded_at": universe.get("computed_at"),
        "aoi": {
            "polygons": [
                {
                    "geometry": p,
                    "kind": "lasso",
                    "drawn_at": None,
                    "area_sq_mi": area,
                }
                for p, area in zip(aoi_polygons, areas, strict=False)
            ]
        },
        "formations": sorted({str(f) for f in formations}),
        "filter_snapshot": filter_snapshot,
        "selection_events": [],
        "universe": universe,
        "partition": None,
        "exclusions": {},
        "manual_exclusions": manual_exclusions,
        "post_save_removals": [],
        "post_save_additions": [],
    }


def compute_draft_buildup(
    *,
    cohort_api10s: list[str],
    provenance: dict[str, Any],
) -> Buildup:
    """Run the one waterfall engine over the synthetic draft provenance."""
    tc = TypeCurve()
    tc.included_api10s = list(cohort_api10s)
    tc.provenance = provenance
    return compute_buildup(tc)
