"""Ramp + Arps evaluator + total-EUR math.

Pins the pieces every caller relies on:
  * evaluate_well_rate is continuous at the seam (rate at t=peak_t
    equals qi from BOTH the ramp branch and the Arps branch).
  * Ramp endpoints: rate at t=0 == qo, rate at t=peak_t == qi.
  * Missing qo / peak_index_months → falls back to pure Arps.
  * compute_total_eur = compute_ramp_eur + compute_eur when ramp
    params are present; equals compute_eur alone when they aren't.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.forecasting.eur import compute_eur
from app.forecasting.ramp_arps import (
    build_ramp_arps_rate,
    compute_ramp_eur,
    compute_total_eur,
    evaluate_fit,
    evaluate_well_rate,
    model_cum_at_t,
)

_BASE_PARAMS = {"qi": 800.0, "Di": 1.2, "b": 1.0, "Df": 0.08}


def test_ramp_endpoint_at_t_zero_equals_qo() -> None:
    t = np.array([0.0])
    rate = evaluate_well_rate(
        qo=200.0, peak_index_months=3, **_BASE_PARAMS, t_years=t
    )
    assert rate[0] == pytest.approx(200.0, rel=1e-6)


def test_ramp_endpoint_at_peak_equals_qi() -> None:
    # Sample exactly at the ramp/Arps seam. Both branches should
    # evaluate to qi — that's the continuity guarantee that prevents
    # a visible jump at the seam in the chart.
    peak_index_months = 3
    peak_t = peak_index_months / 12.0
    t = np.array([peak_t])
    rate = evaluate_well_rate(
        qo=200.0,
        peak_index_months=peak_index_months,
        **_BASE_PARAMS,
        t_years=t,
    )
    assert rate[0] == pytest.approx(_BASE_PARAMS["qi"], rel=1e-3)


def test_ramp_is_linear_between_endpoints() -> None:
    qo = 100.0
    peak_index_months = 4
    peak_t = peak_index_months / 12.0
    # Mid-ramp sample should be exactly halfway between qo and qi.
    t = np.array([peak_t / 2.0])
    rate = evaluate_well_rate(
        qo=qo,
        peak_index_months=peak_index_months,
        **_BASE_PARAMS,
        t_years=t,
    )
    expected_mid = qo + (_BASE_PARAMS["qi"] - qo) * 0.5
    assert rate[0] == pytest.approx(expected_mid, rel=1e-6)


def test_arps_branch_active_past_peak() -> None:
    peak_index_months = 3
    peak_t = peak_index_months / 12.0
    # Two years past peak — well into the Arps tail. Should equal the
    # modified-hyperbolic rate at t=2y past peak, NOT the ramp.
    t = np.array([peak_t + 2.0])
    rate = evaluate_well_rate(
        qo=200.0,
        peak_index_months=peak_index_months,
        **_BASE_PARAMS,
        t_years=t,
    )
    assert 0.0 < rate[0] < _BASE_PARAMS["qi"] / 2.0


def test_no_kink_just_past_seam() -> None:
    # Regression test for the central-difference one-sided artifact:
    # evaluating Arps at arps_t ≈ 0+epsilon used to return ~qi/2
    # (because np.maximum(t - eps, 0) collapsed the central diff into
    # a one-sided diff), so the chart showed a sharp kink at the
    # ramp/Arps seam. Sampling exactly one day past the peak should
    # match qi to within a fraction of a percent — what a typical
    # Arps decline produces over 1/365 of a year.
    peak_index_months = 3
    peak_t = peak_index_months / 12.0
    one_day = 1.0 / 365.0
    t = np.array([peak_t, peak_t + one_day])
    rate = evaluate_well_rate(
        qo=200.0,
        peak_index_months=peak_index_months,
        **_BASE_PARAMS,
        t_years=t,
    )
    # rate[0] is the seam (ramp side, == qi exactly).
    # rate[1] is one day into Arps, should be ~qi*(1-Di/365) for b=1
    # Di=1.2 → drop of ~0.33% from qi. Tolerate 1% so the test isn't
    # sensitive to the exact Di in _BASE_PARAMS.
    assert rate[0] == pytest.approx(_BASE_PARAMS["qi"], rel=1e-6)
    assert rate[1] == pytest.approx(_BASE_PARAMS["qi"], rel=1e-2)


def test_fallback_to_pure_arps_when_qo_missing() -> None:
    t = np.linspace(0.0, 5.0, 60)
    ramp_aware = evaluate_well_rate(
        qo=None, peak_index_months=3, **_BASE_PARAMS, t_years=t
    )
    # No qo → pure Arps from t=0. At t=0 we should get qi exactly.
    assert ramp_aware[0] == pytest.approx(_BASE_PARAMS["qi"], rel=1e-3)


def test_fallback_to_pure_arps_when_peak_index_missing() -> None:
    t = np.linspace(0.0, 5.0, 60)
    rate = evaluate_well_rate(
        qo=200.0, peak_index_months=None, **_BASE_PARAMS, t_years=t
    )
    assert rate[0] == pytest.approx(_BASE_PARAMS["qi"], rel=1e-3)


def test_peak_index_zero_degenerates_to_arps() -> None:
    t = np.linspace(0.0, 5.0, 60)
    rate = evaluate_well_rate(
        qo=200.0, peak_index_months=0, **_BASE_PARAMS, t_years=t
    )
    # peak_index_months <= 0 means "peak at t=0" → no ramp segment.
    assert rate[0] == pytest.approx(_BASE_PARAMS["qi"], rel=1e-3)


def test_build_ramp_arps_rate_monthly_grid_matches_evaluator() -> None:
    # Monthly-grid build helper must agree with the arbitrary-t
    # evaluator at the same grid points — otherwise the TC's saved
    # smoothed_rate would diverge from per-well chart's rate line.
    n_months = 24
    monthly = build_ramp_arps_rate(
        n_months=n_months, qo=200.0, qi=800.0, peak_index=3,
        Di=1.2, b=1.0, Df=0.08,
    )
    t_years = np.arange(n_months, dtype=float) / 12.0
    direct = evaluate_well_rate(
        qo=200.0, peak_index_months=3,
        qi=800.0, Di=1.2, b=1.0, Df=0.08,
        t_years=t_years,
    )
    np.testing.assert_allclose(monthly, direct, rtol=1e-9)


def test_compute_ramp_eur_trapezoid() -> None:
    # Qo=100, qi=900, 6 months → average 500 BOPD for 6 months.
    # Match the production constant from app.forecasting.eur — 365.0
    # days/year, not 365.25 — so the test pins the actual math
    # instead of a "should be" approximation.
    from app.forecasting.eur import DAYS_PER_YEAR
    days_per_month = DAYS_PER_YEAR / 12.0
    expected = 500.0 * 6 * days_per_month
    actual = compute_ramp_eur(qo=100.0, qi=900.0, peak_index=6)
    assert actual == pytest.approx(expected, rel=1e-9)


def test_compute_ramp_eur_zero_when_peak_index_nonpositive() -> None:
    assert compute_ramp_eur(qo=100.0, qi=800.0, peak_index=0) == 0.0


def test_compute_total_eur_adds_ramp_when_present() -> None:
    # Total horizon stays 50 years end-to-end: the ramp consumes
    # peak_t years up front, and the Arps tail covers (horizon -
    # peak_t) years after that. compute_total_eur must reflect this
    # piecewise split — otherwise ``remaining = total -
    # model_cum_at_t(horizon)`` is non-zero at t == horizon, which
    # makes Method-1 EUR drift in the modal's stat row.
    peak_index_months = 3
    params = {**_BASE_PARAMS, "qo": 200.0, "peak_index_months": peak_index_months}
    horizon = 50.0
    total = compute_total_eur(
        model_type="modified_hyperbolic", params=params, horizon_years=horizon,
    )
    arps_post_peak = compute_eur(
        "modified_hyperbolic", params, horizon_years=horizon - peak_index_months / 12.0
    )
    ramp = compute_ramp_eur(
        qo=200.0, qi=_BASE_PARAMS["qi"], peak_index=peak_index_months
    )
    assert total == pytest.approx(arps_post_peak + ramp, rel=1e-9)


def test_evaluate_fit_eur_matches_compute_total_eur() -> None:
    # evaluate_fit powers the TC /preview and save-with-override paths;
    # its eur_per_unit must equal compute_total_eur for the same params.
    # Before the fix, evaluate_fit integrated the Arps tail over the FULL
    # horizon and then added ramp_eur, double-counting the peak_t window.
    # The overlap is a far-tail slice (t≈horizon-peak_t..horizon, where
    # the rate is near-terminal) so it is tiny in magnitude, but it
    # desyncs ``remaining = total - model_cum_at_t`` at t == horizon and
    # makes the manual-override EUR disagree with the auto-fit convention.
    peak_index_months = 3
    params = {**_BASE_PARAMS, "qo": 200.0, "peak_index_months": peak_index_months}
    horizon = 50.0
    expected = compute_total_eur(
        model_type="modified_hyperbolic", params=params, horizon_years=horizon,
    )
    fitted = evaluate_fit(
        qi=_BASE_PARAMS["qi"], Di=_BASE_PARAMS["Di"], b=_BASE_PARAMS["b"],
        Df=_BASE_PARAMS["Df"], qo=200.0, peak_index=peak_index_months,
        n_months=600, horizon_years=horizon,
    )
    assert fitted["eur_per_unit"] == pytest.approx(expected, rel=1e-9)
    # The two reported pieces must sum to the reported total.
    assert fitted["ramp_eur"] + fitted["arps_eur"] == pytest.approx(
        fitted["eur_per_unit"], rel=1e-12
    )


def test_compute_total_eur_falls_back_to_arps_when_missing() -> None:
    # No qo / peak_index_months → compute_total_eur should equal
    # compute_eur. Preserves the EUR semantic for pre-ramp rows.
    total = compute_total_eur(
        model_type="modified_hyperbolic", params=_BASE_PARAMS
    )
    arps = compute_eur("modified_hyperbolic", _BASE_PARAMS)
    assert total == pytest.approx(arps, rel=1e-9)


# -------------------- model_cum_at_t (Method-1 EUR helper) --------------------


def test_model_cum_at_t_zero() -> None:
    cum = model_cum_at_t(
        qo=200.0, peak_index_months=3,
        **_BASE_PARAMS, t_years=0.0,
    )
    assert cum == 0.0


def test_model_cum_at_t_at_peak_equals_ramp_eur() -> None:
    peak_index_months = 3
    peak_t = peak_index_months / 12.0
    cum = model_cum_at_t(
        qo=200.0, peak_index_months=peak_index_months,
        **_BASE_PARAMS, t_years=peak_t,
    )
    ramp = compute_ramp_eur(
        qo=200.0, qi=_BASE_PARAMS["qi"], peak_index=peak_index_months
    )
    assert cum == pytest.approx(ramp, rel=1e-9)


def test_model_cum_at_t_partial_ramp_trapezoid() -> None:
    # Mid-ramp: t = peak_t/2. Rate ramps qo→qi linearly; integral of
    # the partial ramp over [0, peak_t/2] is the average of qo and
    # (qo+qi)/2 times peak_t/2 (the trapezoid rule applied to the
    # closed-form ramp).
    peak_index_months = 6
    peak_t = peak_index_months / 12.0
    qo, qi = 100.0, 800.0
    rate_mid = qo + (qi - qo) * 0.5
    expected = ((qo + rate_mid) / 2.0) * (peak_t / 2.0) * 365.0
    cum = model_cum_at_t(
        qo=qo, peak_index_months=peak_index_months,
        qi=qi, Di=1.2, b=1.0, Df=0.08, t_years=peak_t / 2.0,
    )
    assert cum == pytest.approx(expected, rel=1e-9)


def test_model_cum_at_t_at_horizon_equals_total_eur() -> None:
    # Method-1 sanity: integrating the model to the 50-yr horizon
    # must return compute_total_eur exactly. Otherwise eur_remaining
    # (= total - model_cum_at_endofhistory) would be biased.
    params = {**_BASE_PARAMS, "qo": 200.0, "peak_index_months": 3}
    cum = model_cum_at_t(
        qo=200.0, peak_index_months=3,
        **_BASE_PARAMS, t_years=50.0,
    )
    total = compute_total_eur(
        model_type="modified_hyperbolic", params=params, horizon_years=50.0,
    )
    assert cum == pytest.approx(total, rel=1e-9)


def test_model_cum_at_t_pure_arps_when_ramp_missing() -> None:
    # With ramp params absent, model_cum_at_t reduces to compute_eur
    # at the same horizon.
    t = 2.0
    cum = model_cum_at_t(
        qo=None, peak_index_months=None,
        **_BASE_PARAMS, t_years=t,
    )
    expected = compute_eur("modified_hyperbolic", _BASE_PARAMS, horizon_years=t)
    assert cum == pytest.approx(expected, rel=1e-9)
