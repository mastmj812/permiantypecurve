"""Regression: Novi terminal exponential segments ship d_nom = NULL.

Mirror of erebor's test_arps_tail.py — the two implementations
(`_tail_rates` here, `_tail_values` in erebor) are a cross-repo contract
and must stay behaviorally identical. The pre-2026-09-17 NULL->0
coalesce made the comparison series' tail run FLAT at the terminal
q_start from ~month 360, inflating the dossier cum panels and the
workbook novi_comparison vectors.
"""

from __future__ import annotations

from pytest import approx

from app.warehouse_client.intel_forecast import _seg_decline_per_year, _tail_rates

# Real segment shape from curated.intel_arps ('2 Mile A 100 BRN 1', oil):
_TERMINAL = {
    "segment_curve_type": "exponential",
    "b": 0.0,
    "d_nom": None,
    "q_start": 16.3502883911133,
    "q_stop": 2.2589738368988,
    "day_start": 6573,
    "day_stop": 18250,
}


def test_null_dnom_derives_endpoint_decline() -> None:
    # ln(16.35/2.259) * 365 / (18250-6573) = 0.0619/yr — matches the
    # share's per-day d_eff_tangent (0.0001695 * 365).
    assert _seg_decline_per_year(_TERMINAL) == approx(0.0619, rel=0.01)


def test_null_dnom_tail_declines_not_flat() -> None:
    vals = _tail_rates([_TERMINAL], [6573.0, 10800.0, 18250.0])
    assert vals[0] == approx(_TERMINAL["q_start"], rel=1e-6)  # continuous at seam
    assert vals[1] == approx(8.0, rel=0.05)  # mid-tail, was 16.35 flat pre-fix
    assert vals[2] == approx(_TERMINAL["q_stop"], rel=0.01)  # lands on q_stop


def test_median_series_tail_is_anchored_and_continuous() -> None:
    """End-to-end through _median_series_from_rows: the stitched series
    must be continuous at the forecast->tail seam (no step up), with the
    tail declining. Forecast rows deliberately sit ~0.87x the Arps level
    (the real Novi calendar-day offset)."""
    from app.warehouse_client.intel_forecast import _median_series_from_rows

    by_name = {"W1": (1, "PUD", 1000.0)}  # ll_ft 1000 -> per-kft = raw rates
    # Forecast rows on the terminal-decline slope, at 0.87x the Arps level,
    # ending at mop 359 (the share's ~30-yr horizon).
    d_day = 0.0619 / 365.0
    fc_rows = []
    for mop in range(1, 360):
        day = mop * 30
        rate = 0.87 * _TERMINAL["q_start"] * pow(2.718281828, -d_day * (day - _TERMINAL["day_start"]))
        fc_rows.append(("W1", mop, rate, 0.0, 0.0))
    arps_rows = [dict(_TERMINAL, novi_wellname="W1", production_stream="oil")]

    s = _median_series_from_rows(by_name, [], fc_rows, arps_rows)
    assert s is not None
    rates = [v / 30.0 for v in s.oil_bbl]  # volumes back to rates
    seam_ratio = rates[359] / rates[358]  # mop 360 / mop 359
    assert 0.93 < seam_ratio < 1.0  # continuous-and-declining, no step up
    assert rates[400] < rates[359]  # tail keeps declining


def test_populated_dnom_unchanged() -> None:
    assert _seg_decline_per_year(dict(_TERMINAL, d_nom=0.4)) == 0.4


def test_unusable_endpoints_stay_zero() -> None:
    assert _seg_decline_per_year(dict(_TERMINAL, q_stop=0.0)) == 0.0
