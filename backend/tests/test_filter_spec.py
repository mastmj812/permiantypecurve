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
    spec = parse_filter_query(
        None, None, None, "PDP,SI,NONSENSE,PA", None, None, None, None
    )
    assert spec.statuses == (WellStatus.PDP, WellStatus.SI, WellStatus.PA)


def test_empty_statuses_falls_back_to_pdp() -> None:
    spec = parse_filter_query(None, None, None, "", None, None, None, None)
    assert spec.statuses == (WellStatus.PDP,)
    spec2 = parse_filter_query(None, None, None, "JUNK,NONSENSE", None, None, None, None)
    assert spec2.statuses == (WellStatus.PDP,)


def test_date_and_lateral_bounds_passthrough() -> None:
    spec = parse_filter_query(
        None, None, None, None,
        first_prod_start=date(2018, 1, 1),
        first_prod_end=date(2025, 12, 31),
        lateral_min_ft=5000.0, lateral_max_ft=12000.0,
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

    return str(
        and_(*spec.to_sqlalchemy_clauses()).compile(
            compile_kwargs={"literal_binds": True}
        )
    )


def test_spacing_bounds_exclude_null_and_sentinel() -> None:
    spec = FilterSpec(spacing_min_ft=400.0, spacing_max_ft=1000.0)
    sql = _compiled_clauses(spec)
    # Real-spacing gate: NULL and the 2800 no-neighbor sentinel are out.
    assert "lateral_closer_xy_ft IS NOT NULL" in sql
    assert "lateral_closer_xy_ft != 2800.0" in sql
    assert "lateral_closer_xy_ft >= 400.0" in sql
    assert "lateral_closer_xy_ft <= 1000.0" in sql
    assert " OR " not in sql  # no unbounded re-admission by default


def test_spacing_include_unbounded_readmits_null_and_sentinel() -> None:
    spec = FilterSpec(spacing_min_ft=1500.0, spacing_include_unbounded=True)
    sql = _compiled_clauses(spec)
    assert "lateral_closer_xy_ft IS NULL" in sql
    assert "lateral_closer_xy_ft = 2800.0" in sql
    assert " OR " in sql


def test_spacing_inert_without_bounds() -> None:
    # The toggle alone must not add a clause — with no range set, all
    # wells pass (today's behavior).
    spec = FilterSpec(spacing_include_unbounded=True)
    assert len(spec.to_sqlalchemy_clauses()) == 1  # statuses only


def test_spacing_parse_and_dict_roundtrip() -> None:
    spec = parse_filter_query(
        None, None, None, None, None, None, None, None,
        spacing_min_ft=600.0, spacing_max_ft=1200.0,
        spacing_include_unbounded=True,
    )
    assert spec.spacing_min_ft == 600.0
    assert spec.spacing_max_ft == 1200.0
    assert spec.spacing_include_unbounded is True
    d = filter_spec_dict(spec)
    assert d["spacing_min_ft"] == 600.0
    assert d["spacing_max_ft"] == 1200.0
    assert d["spacing_include_unbounded"] is True
