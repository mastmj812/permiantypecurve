"""Filter spec parsing — DB-free unit tests."""

from __future__ import annotations

from datetime import date

from app.db.models import WellStatus
from app.wells_api.filters import (
    FilterSpec,
    filter_spec_dict,
    parse_filter_query,
)


def test_default_status_is_pdp_only() -> None:
    spec = parse_filter_query(None, None, None, None, None, None, None, None)
    assert spec.statuses == (WellStatus.PDP,)
    assert spec.formations == ()
    assert spec.operators == ()
    assert spec.first_prod_start is None


def test_csv_splitting() -> None:
    spec = parse_filter_query(
        formations="Wolfcamp A, Wolfcamp B,  Bone Spring 2nd  ",
        operators="Op A,Op B",
        counties=None,
        statuses=None,
        first_prod_start=None,
        first_prod_end=None,
        lateral_min_ft=None,
        lateral_max_ft=None,
    )
    assert spec.formations == ("Wolfcamp A", "Wolfcamp B", "Bone Spring 2nd")
    assert spec.operators == ("Op A", "Op B")


def test_status_parsing_drops_unknown_codes() -> None:
    spec = parse_filter_query(None, None, None, "PDP,SI,NONSENSE,PA", None, None, None, None)
    assert spec.statuses == (WellStatus.PDP, WellStatus.SI, WellStatus.PA)


def test_empty_statuses_falls_back_to_pdp() -> None:
    spec = parse_filter_query(None, None, None, "", None, None, None, None)
    assert spec.statuses == (WellStatus.PDP,)
    spec2 = parse_filter_query(None, None, None, "JUNK,NONSENSE", None, None, None, None)
    assert spec2.statuses == (WellStatus.PDP,)


def test_date_and_lateral_bounds_passthrough() -> None:
    spec = parse_filter_query(
        None,
        None,
        None,
        None,
        first_prod_start=date(2018, 1, 1),
        first_prod_end=date(2025, 12, 31),
        lateral_min_ft=5000.0,
        lateral_max_ft=12000.0,
    )
    assert spec.first_prod_start == date(2018, 1, 1)
    assert spec.lateral_min_ft == 5000.0
    assert spec.lateral_max_ft == 12000.0


def test_dict_roundtrip_shape() -> None:
    spec = FilterSpec(
        formations=("Wolfcamp A",),
        operators=("Op A",),
        statuses=(WellStatus.PDP, WellStatus.SI),
        first_prod_start=date(2020, 1, 1),
        lateral_min_ft=7500,
    )
    d = filter_spec_dict(spec)
    assert d["formations"] == ["Wolfcamp A"]
    assert d["statuses"] == ["PDP", "SI"]
    assert d["first_prod_start"] == "2020-01-01"
    assert d["lateral_min_ft"] == 7500


def test_to_sqlalchemy_clauses_is_empty_for_no_op_spec() -> None:
    # No filters and default status → exactly one clause (status IN (PDP)).
    # The tile/selection callers AND in their spatial predicate separately.
    spec = FilterSpec()
    clauses = spec.to_sqlalchemy_clauses()
    assert len(clauses) == 1  # statuses


# ---------------- spacing (LateralCloserXY) ----------------


def _compiled_clauses(spec: FilterSpec) -> str:
    from sqlalchemy import and_

    return str(and_(*spec.to_sqlalchemy_clauses()).compile(compile_kwargs={"literal_binds": True}))


def test_spacing_range_binds_measured_only_and_keeps_both_classes() -> None:
    # A range constrains the `measured` class; the two no-spacing classes
    # ride along on their own (default-on) flags rather than being swept
    # out by the range.
    spec = FilterSpec(spacing_min_ft=400.0, spacing_max_ft=1000.0)
    sql = _compiled_clauses(spec)
    assert "lateral_closer_xy_ft IS NOT NULL" in sql
    assert "lateral_closer_xy_ft != 2800.0" in sql
    assert "lateral_closer_xy_ft >= 400.0" in sql
    assert "lateral_closer_xy_ft <= 1000.0" in sql
    assert "lateral_closer_xy_ft IS NULL" in sql
    assert "lateral_closer_xy_ft = 2800.0" in sql


def test_spacing_classes_excluded_leaves_measured_gate_only() -> None:
    spec = FilterSpec(
        spacing_min_ft=400.0,
        spacing_max_ft=1000.0,
        spacing_include_no_neighbor=False,
        spacing_include_no_data=False,
    )
    sql = _compiled_clauses(spec)
    assert "lateral_closer_xy_ft IS NOT NULL" in sql
    assert "lateral_closer_xy_ft != 2800.0" in sql
    assert " OR " not in sql  # nothing re-admitted


def test_spacing_classes_are_live_without_a_range() -> None:
    # The regression this whole split exists for: an unchecked class box
    # must remove wells even with no min/max set. Previously the flag was
    # inert without a range, so the map showed — and the lasso staged —
    # standalone wells the engineer believed were filtered out.
    spec = FilterSpec(spacing_include_no_neighbor=False)
    sql = _compiled_clauses(spec)
    assert "lateral_closer_xy_ft != 2800.0" in sql
    assert "lateral_closer_xy_ft IS NULL" in sql  # no_data still admitted
    assert len(spec.to_sqlalchemy_clauses()) == 2  # statuses + spacing

    spec_nd = FilterSpec(spacing_include_no_data=False)
    sql_nd = _compiled_clauses(spec_nd)
    assert "lateral_closer_xy_ft IS NOT NULL" in sql_nd
    assert "lateral_closer_xy_ft = 2800.0" in sql_nd  # no_neighbor admitted


def test_spacing_noop_when_unconstrained() -> None:
    # Both classes in and no range = no clause at all, so the unfiltered
    # tile URL and its ETag are unchanged by the class split.
    spec = FilterSpec()
    assert len(spec.to_sqlalchemy_clauses()) == 1  # statuses only


# ---------------- well name (contains) ----------------


def test_well_name_contains_is_case_insensitive_substring() -> None:
    spec = FilterSpec(well_name_contains="University")
    sql = _compiled_clauses(spec)
    assert "lower(wells.name) LIKE lower('%University%')" in sql


def test_well_name_metacharacters_match_literally() -> None:
    spec = FilterSpec(well_name_contains="7-43_2H 100%")
    sql = _compiled_clauses(spec)
    # % and _ in user input are escaped — no accidental wildcards.
    assert "7-43\\_2H 100\\%" in sql
    assert "ESCAPE" in sql


def test_well_name_blank_is_no_filter() -> None:
    spec = parse_filter_query(
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        well_name_contains="   ",
    )
    assert spec.well_name_contains is None
    assert len(spec.to_sqlalchemy_clauses()) == 1  # statuses only


def test_well_name_parse_and_dict_roundtrip() -> None:
    spec = parse_filter_query(
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        well_name_contains="  UNIVERSITY ",
    )
    assert spec.well_name_contains == "UNIVERSITY"
    assert filter_spec_dict(spec)["well_name_contains"] == "UNIVERSITY"


def test_spacing_parse_and_dict_roundtrip() -> None:
    spec = parse_filter_query(
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        spacing_min_ft=600.0,
        spacing_max_ft=1200.0,
        spacing_include_no_neighbor=False,
        spacing_include_no_data=True,
    )
    assert spec.spacing_min_ft == 600.0
    assert spec.spacing_max_ft == 1200.0
    assert spec.spacing_include_no_neighbor is False
    assert spec.spacing_include_no_data is True
    d = filter_spec_dict(spec)
    assert d["spacing_min_ft"] == 600.0
    assert d["spacing_max_ft"] == 1200.0
    assert d["spacing_include_no_neighbor"] is False
    assert d["spacing_include_no_data"] is True


def test_spacing_classes_default_to_included() -> None:
    # Unspecified query params must mean "show everything" — the default
    # has to match the pre-split unfiltered map exactly.
    spec = parse_filter_query(None, None, None, None, None, None, None, None)
    assert spec.spacing_include_no_neighbor is True
    assert spec.spacing_include_no_data is True
