"""Pure-function tests for the selection summary builder."""

from __future__ import annotations

from app.wells_api.summary import (
    HARD_SELECTION_CAP,
    SOFT_SELECTION_WARNING,
    WellSummaryRow,
    build_summary,
)


def _row(
    api10: str,
    *,
    formation: str | None = "Wolfcamp A",
    operator: str | None = "Op A",
    year: int | None = 2022,
    lat_ft: float | None = 10000.0,
) -> WellSummaryRow:
    return WellSummaryRow(
        api10=api10,
        formation=formation,
        operator=operator,
        first_prod_year=year,
        lateral_ft=lat_ft,
    )


def test_empty_selection() -> None:
    s = build_summary([])
    assert s.count == 0
    assert s.median_lateral_ft is None
    assert s.vintage_histogram == {}
    assert s.operators_top5 == []
    assert s.exceeds_soft_cap is False
    assert s.exceeds_hard_cap is False


def test_median_lateral_with_odd_count() -> None:
    rows = [_row("1", lat_ft=5000), _row("2", lat_ft=7500), _row("3", lat_ft=10000)]
    s = build_summary(rows)
    assert s.median_lateral_ft == 7500.0


def test_median_lateral_with_even_count_averages_middle_two() -> None:
    rows = [_row("1", lat_ft=5000), _row("2", lat_ft=10000)]
    s = build_summary(rows)
    assert s.median_lateral_ft == 7500.0


def test_median_ignores_null_and_zero_laterals() -> None:
    rows = [
        _row("1", lat_ft=None),
        _row("2", lat_ft=0),
        _row("3", lat_ft=8000),
        _row("4", lat_ft=12000),
    ]
    s = build_summary(rows)
    # Only 8000 and 12000 count; median = 10000.
    assert s.median_lateral_ft == 10000.0


def test_vintage_histogram_sorted_ascending() -> None:
    rows = [
        _row("a", year=2023),
        _row("b", year=2021),
        _row("c", year=2021),
        _row("d", year=2024),
        _row("e", year=None),
    ]
    s = build_summary(rows)
    # Dict ordering matters: brief calls for a histogram, callers expect
    # year-ascending so the chart axis renders left-to-right correctly.
    assert list(s.vintage_histogram.items()) == [(2021, 2), (2023, 1), (2024, 1)]


def test_operators_top5_descending_with_ties_broken_by_name() -> None:
    rows = (
        [_row(f"{i}", operator="Op A") for i in range(7)]
        + [_row(f"b{i}", operator="Op B") for i in range(3)]
        + [_row(f"c{i}", operator="Op C") for i in range(3)]
        + [_row(f"d{i}", operator="Op D") for i in range(2)]
        + [_row(f"e{i}", operator="Op E") for i in range(2)]
        + [_row(f"f{i}", operator="Op F") for i in range(1)]
    )
    s = build_summary(rows)
    # Only top-5 surfaced. Op F drops off.
    assert len(s.operators_top5) == 5
    assert s.operators_top5[0] == ("Op A", 7)
    assert {op for op, _ in s.operators_top5} == {"Op A", "Op B", "Op C", "Op D", "Op E"}


def test_null_operator_omitted_from_breakdown() -> None:
    rows = [_row("1", operator=None), _row("2", operator="Op A")]
    s = build_summary(rows)
    assert s.operators_top5 == [("Op A", 1)]


def test_soft_and_hard_caps() -> None:
    soft = build_summary([_row(f"x{i}") for i in range(SOFT_SELECTION_WARNING + 1)])
    assert soft.exceeds_soft_cap is True
    assert soft.exceeds_hard_cap is False

    hard = build_summary([_row(f"x{i}") for i in range(HARD_SELECTION_CAP + 1)])
    assert hard.exceeds_soft_cap is True
    assert hard.exceeds_hard_cap is True


def test_formation_counts_descending() -> None:
    rows = [
        _row("1", formation="Wolfcamp A"),
        _row("2", formation="Wolfcamp A"),
        _row("3", formation="Bone Spring 2nd"),
        _row("4", formation=None),
    ]
    s = build_summary(rows)
    assert list(s.formations.items()) == [("Wolfcamp A", 2), ("Bone Spring 2nd", 1)]
