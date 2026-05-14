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
