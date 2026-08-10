"""Type-well build-up provenance — capture-side tests.

House style (see test_deal_polygons.py): the PostGIS round-trip is
covered by the manual verification path in the plan; here we pin the
pure pieces —

  * reason-code validation (API surface returns 422 on unknown codes)
  * SelectionEventIn polygon vertex cap
  * universe_stmt predicate shape: formation-only filter, OR'd
    polygons, sanity cap (NOT the 500-well selection cap), and no
    vintage/lateral/status clauses
  * _build_provenance assembly (mocked session): AOI extraction with
    areas, formations from included wells, verbatim events, stamped
    exclusions, empty post-save ledgers
  * _record_membership_provenance ledger semantics: coded removals,
    additions, both cancellation directions, no-op on {} provenance
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.api.type_curves import (
    ExclusionEntryIn,
    ProvenanceIn,
    SaveRequest,
    SelectionEventIn,
    _build_provenance,
    _record_membership_provenance,
)
from app.db.models import TypeCurve, WellStatus
from app.type_curves.reason_codes import REVIEW_REASON_CODES, validate_code
from app.type_curves.universe import UNIVERSE_SANITY_CAP, universe_stmt

SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
}


# ---------------- reason codes ----------------


def test_validate_code_accepts_every_published_code() -> None:
    for code in REVIEW_REASON_CODES:
        assert validate_code(code) == code


def test_validate_code_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown reason code"):
        validate_code("gut_feel")


def test_exclusion_entry_rejects_unknown_code() -> None:
    # Pydantic surfaces the ValueError as a ValidationError → 422 at the API.
    with pytest.raises(ValidationError):
        ExclusionEntryIn(code="gut_feel", note=None)


def test_exclusion_entry_accepts_code_with_note() -> None:
    e = ExclusionEntryIn(code="parent_child_spacing", note="frac hit from Smith 2H")
    assert e.code == "parent_child_spacing"


# ---------------- selection events ----------------


def test_selection_event_vertex_cap() -> None:
    mega = {
        "type": "Polygon",
        "coordinates": [[[float(i), 0.0] for i in range(2001)]],
    }
    with pytest.raises(ValidationError, match="vertices"):
        SelectionEventIn(kind="polygon", at="2026-08-06T00:00:00Z", polygon=mega)


def test_selection_event_accepts_normal_lasso() -> None:
    ev = SelectionEventIn(
        kind="polygon",
        at="2026-08-06T00:00:00Z",
        polygon=SQUARE,
        api10s=["4200000001"],
    )
    assert ev.polygon is not None


# ---------------- universe statement shape ----------------


def _compiled_universe_sql(n_polygons: int = 1) -> str:
    stmt = universe_stmt([SQUARE] * n_polygons, ["WOLFCAMP A"])
    return str(stmt.compile(compile_kwargs={"literal_binds": False}))


def test_universe_stmt_filters_formation_only() -> None:
    sql = _compiled_universe_sql()
    where = sql.split("WHERE", 1)[1]
    assert "ST_Intersects" in where
    assert "coalesce(wells.wellstick, wells.sh_geom)" in where
    assert "formation_blueox IN" in where
    # The whole point: the universe is pre-filter. None of the cull
    # criteria may appear as predicates.
    for absent in ("first_prod_date", "lateral_ft", "status", "operator", "county"):
        assert absent not in where


def test_universe_stmt_uses_sanity_cap_not_selection_cap() -> None:
    stmt = universe_stmt([SQUARE], ["WOLFCAMP A"])
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    # Cap pulls one over so compute_universe can detect truncation.
    assert f"LIMIT {UNIVERSE_SANITY_CAP + 1}" in sql


def test_universe_stmt_ors_multiple_polygons() -> None:
    sql = _compiled_universe_sql(n_polygons=3)
    assert sql.count("ST_Intersects") == 3
    assert " OR " in sql


# ---------------- _build_provenance assembly ----------------


def _mock_session_for_build(universe_rows: list[Any]) -> MagicMock:
    """Session whose execute() answers, in _build_provenance call order:
    one ST_Area per polygon, then formations, then the universe select."""
    area_result = MagicMock()
    area_result.scalar_one.return_value = 2_589_988.110336  # exactly 1 sq mi

    formations_result = MagicMock()
    formations_result.scalars.return_value = ["WOLFCAMP A", None]

    universe_result = MagicMock()
    universe_result.all.return_value = universe_rows

    session = MagicMock()
    session.execute.side_effect = [area_result, formations_result, universe_result]
    return session


def _universe_row(api10: str) -> MagicMock:
    r = MagicMock()
    r.api10 = api10
    r.name = f"WELL {api10[-2:]}"
    r.operator = "OP A"
    r.formation_blueox = "WOLFCAMP A"
    r.first_prod_date = None
    r.lateral_ft = 9800.0
    r.status = WellStatus.PDP
    return r


def test_build_provenance_none_returns_empty() -> None:
    req = SaveRequest(name="tc", included_api10s=["4200000001"], provenance=None)
    assert _build_provenance(MagicMock(), req) == {}


def test_build_provenance_assembles_v1_shape() -> None:
    req = SaveRequest(
        name="tc",
        filter_spec={"first_prod_start": "2018-01-01", "lateral_min_ft": 6000},
        included_api10s=["4200000001", "4200000002"],
        provenance=ProvenanceIn(
            selection_events=[
                SelectionEventIn(
                    kind="polygon",
                    at="2026-08-06T00:00:00Z",
                    polygon=SQUARE,
                    api10s=["4200000001", "4200000002"],
                ),
                SelectionEventIn(
                    kind="click_add",
                    at="2026-08-06T00:01:00Z",
                    api10s=["4200000002"],
                ),
            ],
            exclusions={"4200000009": ExclusionEntryIn(code="outlier_profile", note="hi GOR")},
        ),
    )
    session = _mock_session_for_build(
        [_universe_row("4200000001"), _universe_row("4200000002"), _universe_row("4200000003")]
    )
    prov = _build_provenance(session, req)

    assert prov["version"] == 1
    assert prov["formations"] == ["WOLFCAMP A"]  # None dropped, sorted
    # One AOI entry (the click event carries no polygon), area attached.
    polys = prov["aoi"]["polygons"]
    assert len(polys) == 1
    assert polys[0]["kind"] == "lasso"
    assert polys[0]["area_sq_mi"] == pytest.approx(1.0)
    # Universe snapshot includes the not-selected 03 well.
    assert prov["universe"]["well_count"] == 3
    assert {w["api10"] for w in prov["universe"]["wells"]} == {
        "4200000001",
        "4200000002",
        "4200000003",
    }
    assert prov["universe"]["wells"][0]["status"] == "PDP"
    # Filter snapshot rides along verbatim; events verbatim; exclusions stamped.
    assert prov["filter_snapshot"]["lateral_min_ft"] == 6000
    assert len(prov["selection_events"]) == 2
    exc = prov["exclusions"]["4200000009"]
    assert exc["code"] == "outlier_profile" and exc["note"] == "hi GOR" and exc["at"]
    assert prov["post_save_removals"] == [] and prov["post_save_additions"] == []


# ---------------- post-save membership ledger ----------------


def _tc_with_provenance(**overrides: Any) -> TypeCurve:
    tc = TypeCurve()
    tc.provenance = {
        "version": 1,
        "post_save_removals": [],
        "post_save_additions": [],
        **overrides,
    }
    return tc


def test_membership_remove_appends_coded_entry() -> None:
    tc = _tc_with_provenance()
    _record_membership_provenance(
        tc,
        old_members=["A", "B"],
        new_members=["A"],
        remove_reason=ExclusionEntryIn(code="data_quality", note="allocation gap"),
    )
    (entry,) = tc.provenance["post_save_removals"]
    assert entry["api10"] == "B"
    assert entry["code"] == "data_quality"
    assert entry["note"] == "allocation gap"
    assert entry["at"]


def test_membership_remove_defaults_to_other_for_api_callers() -> None:
    tc = _tc_with_provenance()
    _record_membership_provenance(tc, old_members=["A", "B"], new_members=["A"], remove_reason=None)
    assert tc.provenance["post_save_removals"][0]["code"] == "other"


def test_membership_readd_cancels_removal() -> None:
    tc = _tc_with_provenance(
        post_save_removals=[{"api10": "B", "code": "other", "note": None, "at": "t0"}]
    )
    _record_membership_provenance(tc, old_members=["A"], new_members=["A", "B"], remove_reason=None)
    assert tc.provenance["post_save_removals"] == []
    # Cancelled, not double-entered as an addition.
    assert tc.provenance["post_save_additions"] == []


def test_membership_remove_cancels_prior_addition() -> None:
    tc = _tc_with_provenance(post_save_additions=[{"api10": "C", "at": "t0"}])
    _record_membership_provenance(tc, old_members=["A", "C"], new_members=["A"], remove_reason=None)
    assert tc.provenance["post_save_additions"] == []
    assert tc.provenance["post_save_removals"] == []


def test_membership_addition_appends_entry() -> None:
    tc = _tc_with_provenance()
    _record_membership_provenance(tc, old_members=["A"], new_members=["A", "D"], remove_reason=None)
    (entry,) = tc.provenance["post_save_additions"]
    assert entry["api10"] == "D" and entry["at"]


def test_membership_noop_on_unrecorded_provenance() -> None:
    tc = TypeCurve()
    tc.provenance = {}
    _record_membership_provenance(tc, old_members=["A", "B"], new_members=["A"], remove_reason=None)
    # Pre-0026 curve: stays honestly empty, no fabricated partial trail.
    assert tc.provenance == {}
