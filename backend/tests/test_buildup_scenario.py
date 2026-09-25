"""Build-up waterfall: development-scenario stage (DB-free).

The scenario stage attributes wells culled by the FilterSpec scenario
clauses (class, subject bench, bench-pair parent, parent age). The waterfall
evaluates them in Python over the universe snapshot — ``scenario_culled``
must say exactly what the SQL says, including SQL NULL semantics (any
comparison against a missing value = not admitted).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.type_curves.buildup import (
    STAGE_DESCRIPTIONS,
    compute_buildup,
    scenario_culled,
    scenario_filters_active,
)
from app.type_curves.universe import parent_facts

from .test_buildup import _full_provenance, _tc, _uni_well


def _w(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "api10": "4200000001",
        "scenario_class": "underfill",
        "scenario_bench": "WCA_1",
        "youngest_parent_age_days": 800,
        "oldest_parent_age_days": 2100,
        "parent_facts": {
            "LSSH": {"off": 210, "dz": -520, "age_min": 800, "age_max": 2100},
            "WCB_1": {"off": 900, "dz": 380, "age_min": 400, "age_max": 400},
        },
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    "fs, culled",
    [
        ({}, False),
        # classes; no_data admits the NULL class only
        ({"scenario_classes": ["underfill"]}, False),
        ({"scenario_classes": ["topfill"]}, True),
        ({"scenario_classes": ["no_data"]}, True),
        # subject bench
        ({"scenario_benches": ["WCA_1"]}, False),
        ({"scenario_benches": ["WCB_2"]}, True),
        # bench pair: LSSH parent 210 ft offset, 520 ft shallower
        ({"parent_benches": ["LSSH"]}, False),
        ({"parent_benches": ["LSSH"], "parent_side": "above"}, False),
        ({"parent_benches": ["LSSH"], "parent_side": "below"}, True),
        ({"parent_benches": ["LSSH"], "parent_dtvd_max_ft": 500}, True),
        ({"parent_benches": ["LSSH"], "parent_dtvd_max_ft": 600}, False),
        # WCB_1 parent sits outside the 660-ft offset gate -> never admits
        ({"parent_benches": ["WCB_1"]}, True),
        # any-of across benches
        ({"parent_benches": ["WCB_1", "LSSH"]}, False),
        # no facts for the bench, or the well's OWN bench, never admit
        ({"parent_benches": ["WCC"]}, True),
        ({"parent_benches": ["WCA_1"]}, True),
        # ages scoped to the named bench...
        ({"parent_benches": ["LSSH"], "parent_age_min_days": 730}, False),
        ({"parent_benches": ["LSSH"], "parent_age_min_days": 900}, True),
        ({"parent_benches": ["LSSH"], "parent_age_max_days": 2000}, True),
        # ...or class-level without a parent bench
        ({"parent_age_min_days": 730}, False),
        ({"parent_age_min_days": 900}, True),
        ({"parent_age_max_days": 2000}, True),
        # side / dTVD cap alone are inert (FilterSpec drops them)
        ({"parent_side": "below", "parent_dtvd_max_ft": 10}, False),
    ],
)
def test_scenario_culled_mirrors_filterspec(fs: dict[str, Any], culled: bool) -> None:
    assert scenario_culled(_w(), fs) is culled


def test_sql_null_semantics() -> None:
    # A well absent from dev_scenario: every scenario comparison fails
    # except an explicit no_data admission.
    blank = _w(
        scenario_class=None,
        scenario_bench=None,
        youngest_parent_age_days=None,
        oldest_parent_age_days=None,
        parent_facts={},
    )
    assert scenario_culled(blank, {"scenario_classes": ["no_data"]}) is False
    assert scenario_culled(blank, {"scenario_classes": ["topfill"]}) is True
    assert scenario_culled(blank, {"parent_age_min_days": 1}) is True
    assert scenario_culled(blank, {"parent_benches": ["LSSH"]}) is True
    # NULL dTVD fails a side test and a cap (NULL > 0 / abs(NULL) <= x)
    nodz = _w(parent_facts={"LSSH": {"off": 100, "dz": None, "age_min": 800, "age_max": 800}})
    assert scenario_culled(nodz, {"parent_benches": ["LSSH"]}) is False
    assert scenario_culled(nodz, {"parent_benches": ["LSSH"], "parent_side": "above"}) is True
    assert scenario_culled(nodz, {"parent_benches": ["LSSH"], "parent_dtvd_max_ft": 900}) is True


def test_filters_active_ignores_inert_fields() -> None:
    assert not scenario_filters_active({})
    assert not scenario_filters_active({"parent_side": "above", "parent_dtvd_max_ft": 500})
    assert scenario_filters_active({"scenario_classes": ["topfill"]})
    assert scenario_filters_active({"parent_age_max_days": 3000})


def test_parent_facts_keeps_other_bench_parents_only() -> None:
    ctx = {
        "WCA_1": {"n_parent": 3, "parent_min_offset_ft": 50},  # own bench
        "LSSH": {
            "n_parent": 2,
            "parent_min_offset_ft": 210,
            "parent_nearest_dtvd_ft": -520,
            "parent_min_age_days": 800,
            "parent_max_age_days": 2100,
            "n_codev": 1,
        },
        "WCB_1": {"n_parent": 0, "n_codev": 4},  # codev only
    }
    assert parent_facts(ctx, "WCA_1") == {
        "LSSH": {"off": 210, "dz": -520, "age_min": 800, "age_max": 2100}
    }
    assert parent_facts(None, "WCA_1") == {}


def test_stage_sits_after_spacing_before_filters_other() -> None:
    keys = [k for k, _ in STAGE_DESCRIPTIONS]
    assert keys.index("scenario") == keys.index("spacing") + 1
    assert keys.index("filters_other") == keys.index("scenario") + 1


def _scenario_prov() -> tuple[dict[str, Any], list[str]]:
    """Base fixture with every universe well scenario-aware + underfill,
    plus W30 (topfill, never staged) and W31 (topfill, staged + included)."""
    prov, included = _full_provenance()
    for w in prov["universe"]["wells"]:
        w.update(_w(api10=w["api10"]))
    for api10 in ("W30", "W31"):
        w = _uni_well(api10)
        w.update(_w(api10=api10, scenario_class="topfill"))
        prov["universe"]["wells"].append(w)
        prov["universe"]["well_count"] += 1
    prov["selection_events"][0]["api10s"].append("W31")
    prov["filter_snapshot"]["scenario_classes"] = ["underfill"]
    return prov, [*included, "W31"]


def test_waterfall_attributes_scenario_culls() -> None:
    prov, included = _scenario_prov()
    b = compute_buildup(_tc(prov, included))
    by = {r.api10: r for r in b.rows}
    # never entered the cohort -> culled at the scenario stage
    assert by["W30"].disposition == "scenario"
    # already in the curve -> stays included; the live filter miss is an
    # advisory only (never moves a waterfall count)
    assert by["W31"].disposition == "included"
    assert by["W31"].off_filter_stage == "scenario"
    assert "W31" in b.off_filter_api10s
    culled = {w.stage: w.culled for w in b.waterfall}
    assert culled["scenario"] == 1
    # the rest of the funnel is untouched
    assert culled["vintage"] == 1 and culled["filters_other"] == 1
    assert b.reconciles
    crit = dict(b.header.criteria) if b.header else {}
    assert crit["scenario_criterion"] == "class underfill (at first production)"


def test_earlier_stages_win_over_scenario() -> None:
    # W03 fails vintage AND would fail scenario: first stage that removes wins.
    prov, included = _scenario_prov()
    for w in prov["universe"]["wells"]:
        if w["api10"] == "W03":
            w["scenario_class"] = "topfill"
    b = compute_buildup(_tc(prov, included))
    assert {r.api10: r for r in b.rows}["W03"].disposition == "vintage"


def test_pre_scenario_snapshot_degrades_honestly() -> None:
    # Universe snapshotted before scenario attrs existed + a scenario filter:
    # no cull, and a note says the stage wasn't evaluated.
    prov, included = _full_provenance()
    prov["filter_snapshot"]["scenario_classes"] = ["topfill"]
    b = compute_buildup(_tc(prov, included))
    culled = {w.stage: w.culled for w in b.waterfall}
    assert culled["scenario"] == 0
    assert any("predates scenario attributes" in n for n in b.notes)


def test_no_scenario_filters_no_note_and_zero_row() -> None:
    prov, included = _full_provenance()
    b = compute_buildup(_tc(prov, included))
    assert {w.stage: w.culled for w in b.waterfall}["scenario"] == 0
    assert not any("scenario" in n for n in b.notes)
