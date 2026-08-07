"""Type-well build-up waterfall — pure computation tests.

Synthetic provenance exercising every stage, plus the invariants the
sheet's reconciliation row depends on:

  * every universe well gets exactly one disposition;
  * remaining-after-last-stage + out-of-universe additions == final
    included count;
  * cohort-transferred short wells stay included (annotated), never
    culled at short_history;
  * degraded ({}) and no-AOI provenance render included-only rosters
    with the honest note, never a fabricated funnel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.db.models import AlignmentMethod, NormalizationBasis, TypeCurve
from app.exports.buildup import buildup_csv
from app.type_curves.buildup import (
    TRANSFERRED_ANNOTATION,
    compute_buildup,
)


def _uni_well(
    api10: str,
    *,
    first_prod: str | None = "2020-06-01",
    lateral: float | None = 9500.0,
    status: str | None = "PDP",
    formation: str = "WOLFCAMP A",
) -> dict[str, Any]:
    return {
        "api10": api10,
        "name": f"WELL {api10[-2:]}",
        "operator": "OP A",
        "formation": formation,
        "first_prod_date": first_prod,
        "lateral_ft": lateral,
        "status": status,
    }


def _tc(provenance: dict[str, Any], included: list[str]) -> TypeCurve:
    tc = TypeCurve()
    tc.id = uuid.uuid4()
    tc.name = "buildup_test"
    tc.included_api10s = included
    tc.normalization_basis = NormalizationBasis.PER_LATERAL_FT
    tc.alignment_method = AlignmentMethod.PEAK_RAMP
    tc.series = {}
    tc.created_at = datetime(2026, 8, 1, tzinfo=UTC)
    tc.provenance = provenance
    return tc


def _full_provenance() -> tuple[dict[str, Any], list[str]]:
    """One universe well per stage + two clean survivors + one
    transferred short + one out-of-universe addition.

    W01 clean included            W06 no_peak
    W02 clean included            W07 short_history (culled)
    W03 vintage (2015 first prod) W08 short but TRANSFERRED → included
    W04 lateral (4,100 ft)        W09 review_excluded (frac hit)
    W05 filters_other (SI status) W10 not_selected (never staged)
    W11 post_save_removed         X99 included, outside the universe
    """
    wells = [
        _uni_well("W01"),
        _uni_well("W02"),
        _uni_well("W03", first_prod="2015-03-01"),
        _uni_well("W04", lateral=4100.0),
        _uni_well("W05", status="SI"),
        _uni_well("W06"),
        _uni_well("W07"),
        _uni_well("W08"),
        _uni_well("W09"),
        _uni_well("W10"),
        _uni_well("W11"),
    ]
    staged = ["W01", "W02", "W06", "W07", "W08", "W09", "W11"]
    prov = {
        "version": 1,
        "recorded_at": "2026-08-06T00:00:00Z",
        "aoi": {
            "polygons": [
                {
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                    "kind": "lasso",
                    "drawn_at": "2026-08-05T00:00:00Z",
                    "area_sq_mi": 41.7,
                }
            ]
        },
        "formations": ["WOLFCAMP A"],
        "filter_snapshot": {
            "formations": ["WOLFCAMP A"],
            "operators": [],
            "counties": [],
            "statuses": ["PDP"],
            "first_prod_start": "2018-01-01",
            "first_prod_end": None,
            "lateral_min_ft": 6000,
            "lateral_max_ft": None,
            "api10s": [],
        },
        "selection_events": [
            {
                "kind": "polygon",
                "at": "2026-08-05T00:00:00Z",
                "api10s": staged,
                "polygon": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
                "filters": {},
            }
        ],
        "universe": {
            "computed_at": "2026-08-06T00:00:00Z",
            "well_count": len(wells),
            "wells": wells,
        },
        "partition": {
            "cutoff_months": 6,
            "short": ["W07", "W08"],
            "no_peak": ["W06"],
        },
        "exclusions": {
            "W09": {
                "code": "parent_child_spacing",
                "note": "frac hit from Smith 2H",
                "at": "2026-08-06T00:00:00Z",
            }
        },
        "post_save_removals": [
            {"api10": "W11", "code": "data_quality", "note": None,
             "at": "2026-08-06T01:00:00Z"}
        ],
        "post_save_additions": [
            {"api10": "X99", "at": "2026-08-06T01:00:00Z"}
        ],
    }
    included = ["W01", "W02", "W08", "X99"]
    return prov, included


def test_every_stage_attributed_once() -> None:
    prov, included = _full_provenance()
    b = compute_buildup(_tc(prov, included))

    by_api10 = {r.api10: r for r in b.rows}
    assert len(b.rows) == 11  # every universe well appears exactly once
    assert by_api10["W01"].disposition == "included"
    assert by_api10["W02"].disposition == "included"
    assert by_api10["W03"].disposition == "vintage"
    assert by_api10["W04"].disposition == "lateral"
    assert by_api10["W05"].disposition == "filters_other"
    assert by_api10["W06"].disposition == "no_peak"
    assert by_api10["W07"].disposition == "short_history"
    assert by_api10["W08"].disposition == "included"
    assert by_api10["W09"].disposition == "review_excluded"
    assert by_api10["W10"].disposition == "not_selected"
    assert by_api10["W11"].disposition == "post_save_removed"


def test_transferred_short_annotated_not_culled() -> None:
    prov, included = _full_provenance()
    b = compute_buildup(_tc(prov, included))
    w08 = next(r for r in b.rows if r.api10 == "W08")
    assert w08.disposition == "included"
    assert w08.annotation == TRANSFERRED_ANNOTATION


def test_coded_stages_carry_reason_and_note() -> None:
    prov, included = _full_provenance()
    b = compute_buildup(_tc(prov, included))
    w09 = next(r for r in b.rows if r.api10 == "W09")
    assert w09.reason_code == "parent_child_spacing"
    assert w09.note == "frac hit from Smith 2H"
    w11 = next(r for r in b.rows if r.api10 == "W11")
    assert w11.reason_code == "data_quality"


def test_waterfall_reconciles_to_final_count() -> None:
    prov, included = _full_provenance()
    b = compute_buildup(_tc(prov, included))

    # remaining after the last stage = in-universe survivors (3), plus
    # the out-of-universe addition = the final included count (4).
    assert b.waterfall[-1].remaining == 3
    assert b.out_of_universe_included == ["X99"]
    assert b.included_count == 4
    assert b.reconciles

    # Running total is internally consistent: each row's remaining =
    # previous remaining - its culled.
    prev = len(b.rows)
    for w in b.waterfall:
        assert w.remaining == prev - w.culled
        prev = w.remaining


def test_waterfall_stage_counts() -> None:
    prov, included = _full_provenance()
    b = compute_buildup(_tc(prov, included))
    culled = {w.stage: w.culled for w in b.waterfall}
    assert culled == {
        "vintage": 1,
        "lateral": 1,
        "spacing": 0,  # no spacing bounds in this snapshot
        "filters_other": 1,
        "not_selected": 1,
        "no_peak": 1,
        "short_history": 1,
        "review_excluded": 1,
        "post_save_removed": 1,
    }


def test_header_block_facts() -> None:
    prov, included = _full_provenance()
    b = compute_buildup(_tc(prov, included))
    assert b.header is not None
    assert b.header.formations == ["WOLFCAMP A"]
    assert b.header.aoi_polygon_count == 1
    assert b.header.aoi_total_area_sq_mi == 41.7
    assert b.header.universe_count == 11
    assert b.header.cutoff_months == 6
    labels = dict(b.header.criteria)
    assert labels["vintage_criterion"] == "first prod >= 2018-01-01"
    assert labels["lateral_criterion"] == ">= 6,000 ft"
    assert labels["status_criterion"] == "PDP"


def test_null_lateral_only_culled_when_bound_set() -> None:
    prov, included = _full_provenance()
    prov["universe"]["wells"].append(_uni_well("W12", lateral=None))
    prov["universe"]["well_count"] += 1
    b = compute_buildup(_tc(prov, included))
    w12 = next(r for r in b.rows if r.api10 == "W12")
    assert w12.disposition == "lateral"  # lateral_min_ft = 6000 is set

    # Drop the bound → the null-lateral well slides through to
    # not_selected instead (it never entered the cohort).
    prov["filter_snapshot"]["lateral_min_ft"] = None
    b2 = compute_buildup(_tc(prov, included))
    w12b = next(r for r in b2.rows if r.api10 == "W12")
    assert w12b.disposition == "not_selected"


def test_spacing_stage_culls_outside_range_and_unbounded() -> None:
    prov, included = _full_provenance()
    prov["filter_snapshot"]["spacing_min_ft"] = 400
    prov["filter_snapshot"]["spacing_max_ft"] = 1000
    # Base fixture wells get real in-range spacing so they keep their
    # original dispositions (a missing key reads as NULL → unbounded →
    # spacing cull, which is the intended semantics but not this test).
    for w in prov["universe"]["wells"]:
        w["lateral_closer_xy_ft"] = 800.0
    # W20: real spacing outside range; W21: sentinel (no neighbor);
    # W22: absent from WellSpacing (NULL); W23: real spacing in range
    # but never staged → falls through to not_selected.
    for api10, spacing in (
        ("W20", 1600.0), ("W21", 2800.0), ("W22", None), ("W23", 660.0),
    ):
        w = _uni_well(api10)
        w["lateral_closer_xy_ft"] = spacing
        prov["universe"]["wells"].append(w)
        prov["universe"]["well_count"] += 1
    b = compute_buildup(_tc(prov, included))
    by = {r.api10: r for r in b.rows}
    assert by["W20"].disposition == "spacing"  # measured, out of range
    assert by["W21"].disposition == "spacing"  # sentinel, toggle off
    assert by["W22"].disposition == "spacing"  # NULL, toggle off
    assert by["W23"].disposition == "not_selected"
    culled = {w.stage: w.culled for w in b.waterfall}
    assert culled["spacing"] == 3
    labels = dict(b.header.criteria) if b.header else {}
    assert "spacing_criterion" in labels
    assert "no-neighbor wells excluded" in labels["spacing_criterion"]


def test_spacing_include_unbounded_keeps_sentinel_wells() -> None:
    prov, included = _full_provenance()
    prov["filter_snapshot"]["spacing_min_ft"] = 1500
    prov["filter_snapshot"]["spacing_include_unbounded"] = True
    w = _uni_well("W21")
    w["lateral_closer_xy_ft"] = 2800.0  # sentinel — admitted by toggle
    prov["universe"]["wells"].append(w)
    prov["universe"]["well_count"] += 1
    b = compute_buildup(_tc(prov, included))
    w21 = next(r for r in b.rows if r.api10 == "W21")
    # Survives spacing, then falls to not_selected (never staged).
    assert w21.disposition == "not_selected"
    labels = dict(b.header.criteria) if b.header else {}
    assert "no-neighbor wells included" in labels["spacing_criterion"]


def test_no_spacing_filter_means_no_spacing_stage() -> None:
    prov, included = _full_provenance()  # snapshot has no spacing bounds
    b = compute_buildup(_tc(prov, included))
    assert all(w.stage != "spacing" for w in b.waterfall) or (
        next(w for w in b.waterfall if w.stage == "spacing").culled == 0
    )


def test_degraded_empty_provenance() -> None:
    tc = _tc({}, ["A", "B"])
    b = compute_buildup(tc)
    assert b.degraded
    assert b.header is None
    assert b.waterfall == []
    assert [r.api10 for r in b.rows] == ["A", "B"]
    assert all(r.disposition == "included" for r in b.rows)
    assert any("provenance not recorded" in n for n in b.notes)


def test_no_aoi_provenance_falls_back_to_membership() -> None:
    prov, included = _full_provenance()
    prov["universe"] = {"computed_at": "2026-08-06", "well_count": 0, "wells": []}
    prov["aoi"] = {"polygons": []}
    b = compute_buildup(_tc(prov, included))
    assert not b.degraded
    assert b.waterfall == []
    assert [r.api10 for r in b.rows] == included
    assert any("no AOI recorded" in n for n in b.notes)


def test_unaccounted_exposed_not_hidden() -> None:
    # A universe well that passes every filter, sits in the cohort,
    # but is absent from included with no exclusion/partition record —
    # membership drift the record can't explain.
    prov, included = _full_provenance()
    prov["universe"]["wells"].append(_uni_well("W13"))
    prov["universe"]["well_count"] += 1
    prov["selection_events"][0]["api10s"].append("W13")
    b = compute_buildup(_tc(prov, included))
    w13 = next(r for r in b.rows if r.api10 == "W13")
    assert w13.disposition == "unaccounted"
    assert any(w.stage == "unaccounted" for w in b.waterfall)
    assert b.reconciles  # unaccounted is a stage, so the math closes


def test_buildup_csv_shape() -> None:
    prov, included = _full_provenance()
    csv_text = buildup_csv(_tc(prov, included))
    lines = csv_text.strip().splitlines()
    # Comment blocks first, then the roster header + 11 universe rows
    # + 1 out-of-universe row.
    assert any(line.startswith("# curve_name") for line in lines)
    assert any(line.startswith("# final_cohort") for line in lines)
    header_idx = next(
        i for i, line in enumerate(lines) if line.startswith("api10,")
    )
    roster = lines[header_idx + 1 :]
    assert len(roster) == 11 + 1
    assert any("added outside AOI/universe" in line for line in roster)
    # Included rows sort first.
    assert roster[0].startswith("W01")
