"""Pure-function tests for type-curve aggregation."""

from __future__ import annotations

import pytest

from app.type_curves.aggregate import (
    DAYS_PER_MONTH,
    WellSeries,
    aggregate,
)


def _well(
    api10: str,
    *,
    lateral_ft: float | None = 10_000.0,
    oil_rates: list[float | None] | None = None,
    gas_rates: list[float | None] | None = None,
    water_rates: list[float | None] | None = None,
    n: int = 12,
    flat_oil: float | None = None,
) -> WellSeries:
    if oil_rates is None:
        oil_rates = [flat_oil] * n if flat_oil is not None else [None] * n
    if gas_rates is None:
        gas_rates = [None] * n
    if water_rates is None:
        water_rates = [None] * n
    return WellSeries(
        api10=api10,
        lateral_ft=lateral_ft,
        proppant_lbs=None,
        oil_rates=oil_rates,
        gas_rates=gas_rates,
        water_rates=water_rates,
    )


def test_ragged_stream_lengths_size_panel_to_longest_stream() -> None:
    # Under peak_month alignment water is sliced from its own earlier
    # peak, so its array runs longer than oil. The panel must size to the
    # longest stream (water=10) rather than capping at oil's length (6),
    # which would silently drop the water tail.
    w = _well(
        "1",
        lateral_ft=10_000,
        oil_rates=[500.0] * 6,
        water_rates=[300.0] * 10,
        n=10,  # gas defaults to this length; water is the longest stream
    )
    agg = aggregate([w])
    assert agg.n_months == 10
    # Oil is NaN-padded in the tail (well drops out after month 6).
    assert agg.streams["oil"].p50[:6] == [50.0] * 6
    assert agg.streams["oil"].p50[6:] == [None] * 4
    assert agg.streams["oil"].well_count == [1] * 6 + [0] * 4
    # Water carries its full length with no truncation.
    assert agg.streams["water"].p50 == [30.0] * 10
    assert agg.streams["water"].well_count == [1] * 10


def test_empty_input_returns_empty_aggregate() -> None:
    agg = aggregate([])
    assert agg.n_wells == 0
    assert agg.n_months == 0
    assert agg.streams["oil"].p50 == []


def test_per_1000_ft_normalization_picks_correct_denominator() -> None:
    # One well, 10,000 ft lateral, flat 500 BOPD.
    # Normalized rate should be 500 / (10000/1000) = 50 BOPD per 1000 ft.
    w = _well("1", lateral_ft=10_000, flat_oil=500.0, n=12)
    agg = aggregate([w])
    assert agg.streams["oil"].p50 == [50.0] * 12
    assert agg.streams["oil"].mean == [50.0] * 12
    # Implied EUR p50 = 50 BOPD per 1000 ft × DAYS_PER_MONTH × 12 months
    assert agg.streams["oil"].implied_eur_per_1000ft["p50"] == pytest.approx(
        50.0 * DAYS_PER_MONTH * 12
    )


def test_median_picked_correctly_across_5_wells_with_known_spread() -> None:
    # Five wells, all 10k lateral, flat oil rates 100/200/300/400/500.
    # After /10 normalization: 10/20/30/40/50 per 1000 ft.
    # Each month: P10 ≈ 14, P50 = 30, P90 ≈ 46, mean = 30.
    wells = [_well(str(i), flat_oil=r * 100.0, n=6) for i, r in enumerate([1, 2, 3, 4, 5])]
    agg = aggregate(wells)
    assert agg.n_wells == 5
    for j in range(6):
        assert agg.streams["oil"].p50[j] == pytest.approx(30.0)
        assert agg.streams["oil"].mean[j] == pytest.approx(30.0)
    # well_count should be 5 in every month (all wells have full history).
    assert agg.streams["oil"].well_count == [5] * 6


def test_spe_percentile_orientation_p10_is_high_case() -> None:
    # SPE / PRMS reserves convention: P10 = HIGH case (exceeded by only
    # 10% of wells), P90 = LOW case. Five wells at 10/20/30/40/50
    # per-1000ft normalized → linear-interpolated statistical
    # percentiles: 90th = 46 (→ P10), 75th = 40 (→ P25), median = 30,
    # 25th = 20 (→ P75), 10th = 14 (→ P90).
    wells = [_well(str(i), flat_oil=r * 100.0, n=3) for i, r in enumerate([1, 2, 3, 4, 5])]
    agg = aggregate(wells)
    s = agg.streams["oil"]
    for j in range(3):
        assert s.p10[j] == pytest.approx(46.0)
        assert s.p25[j] == pytest.approx(40.0)
        assert s.p50[j] == pytest.approx(30.0)
        assert s.p75[j] == pytest.approx(20.0)
        assert s.p90[j] == pytest.approx(14.0)
        assert s.p10[j] >= s.p50[j] >= s.p90[j]


def test_well_count_tapers_with_staggered_history() -> None:
    # Three wells: 12 / 6 / 3 months of oil history.
    # Months 0-2: 3 wells. Months 3-5: 2 wells. Months 6-11: 1 well.
    long_w = _well("long", flat_oil=50.0, n=12)
    mid_w = _well("mid", oil_rates=[50.0] * 6 + [None] * 6)
    short_w = _well("short", oil_rates=[50.0] * 3 + [None] * 9)
    agg = aggregate([long_w, mid_w, short_w])
    assert agg.streams["oil"].well_count == [3, 3, 3, 2, 2, 2, 1, 1, 1, 1, 1, 1]


def test_wells_missing_lateral_are_skipped_for_per_lateral_ft() -> None:
    keepable = _well("keep", lateral_ft=10_000, flat_oil=500.0, n=6)
    no_lateral = _well("no_lateral", lateral_ft=None, flat_oil=500.0, n=6)
    zero_lateral = _well("zero", lateral_ft=0, flat_oil=500.0, n=6)
    agg = aggregate(
        [keepable, no_lateral, zero_lateral],
        normalization_basis="per_lateral_ft",
    )
    assert agg.n_wells == 1
    assert agg.streams["oil"].well_count == [1] * 6


def test_n_months_caps_output_length() -> None:
    w = _well("1", flat_oil=100.0, n=24)
    agg = aggregate([w], n_months=6)
    assert agg.n_months == 6
    assert len(agg.streams["oil"].p50) == 6


def test_alignment_method_is_propagated_into_aggregate() -> None:
    """The aggregator is opaque to where the slice started — the loader
    decides the actual cutoff. What the function asserts is that the
    alignment_method round-trips through the result, so callers can persist
    it (and the JSONB metadata stays truthful for the saved curve)."""
    w = _well("1", flat_oil=100.0, n=12)
    for method in ("peak_month", "first_prod_month"):
        agg = aggregate([w], alignment_method=method)  # type: ignore[arg-type]
        assert agg.alignment_method == method


def test_implied_eur_scales_linearly_with_percentile_value() -> None:
    # Two wells, same lateral, one constant at 100 BOPD and one at 300 BOPD.
    # P50 (median) ends up 200 per 1000 ft after /10 normalization → 20.
    # Wait: 100/10=10, 300/10=30 → median is 20.
    wells = [
        _well("low", lateral_ft=10_000, flat_oil=100.0, n=12),
        _well("high", lateral_ft=10_000, flat_oil=300.0, n=12),
    ]
    agg = aggregate(wells)
    eur_p50 = agg.streams["oil"].implied_eur_per_1000ft["p50"]
    # 20 BOPD per 1000ft × 30.44 days × 12 months = 7305.6 BBL per 1000ft
    assert eur_p50 == pytest.approx(20.0 * DAYS_PER_MONTH * 12)
