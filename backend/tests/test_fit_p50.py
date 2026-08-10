"""P50 Arps fit — synthesize a known modified-hyperbolic series and
verify `fit_p50_series` recovers the parameters within tolerance."""

from __future__ import annotations

import numpy as np
import pytest

from app.forecasting.cumulative import cum_modified_hyperbolic
from app.forecasting.eur import DAYS_PER_YEAR
from app.type_curves.fit_p50 import fit_p50_series


def _synthesize_p50(qi: float, Di: float, b: float, Df: float, n_months: int) -> list[float]:
    """Build the synthetic P50 = monthly rate from t_lo to t_hi midpoints."""
    t = (np.arange(n_months, dtype=float) + 0.5) / 12.0
    # rate at midpoint of month i. Computing as derivative of cum gives
    # smoother values than evaluating the rate function at the midpoint
    # (avoids the corner-of-piecewise issue at the switchover).
    eps = 1.0 / 24.0  # half-month
    lo = cum_modified_hyperbolic(np.maximum(t - eps, 0.0), qi, Di, b, Df)
    hi = cum_modified_hyperbolic(t + eps, qi, Di, b, Df)
    rate = (hi - lo) / (2 * eps * DAYS_PER_YEAR)
    return [float(x) for x in rate]


def test_recovers_known_modified_hyperbolic_params() -> None:
    truth = {"qi": 600.0, "Di": 0.85, "b": 1.2, "Df": 0.08}
    p50 = _synthesize_p50(**truth, n_months=48)
    r = fit_p50_series(p50)
    assert r is not None
    # The Arps cum-vs-time fit has a (Di, b) degeneracy — many
    # combinations yield near-identical cum curves over a 4-yr window.
    # The engineer-facing contract is: the fitted curve closely matches
    # the input rate series and the implied EUR is in the right ballpark.
    # We assert on those, plus a loose qi-recovery sanity check.
    assert r["qi"] == pytest.approx(truth["qi"], rel=0.10)
    assert r["Df"] == pytest.approx(truth["Df"], abs=1e-6)
    assert r["r2"] > 0.99
    assert len(r["smoothed_rate"]) == 48
    # Smoothed series starts near peak and declines monotonically.
    assert r["smoothed_rate"][0] > r["smoothed_rate"][-1]
    # The smoothed curve should track the input within a few percent
    # in the bulk of the series (skipping the noisy first month).
    rms_err = float(np.sqrt(np.mean(
        [(s - p) ** 2 for s, p in zip(r["smoothed_rate"][1:], p50[1:], strict=False)]
    )))
    mean_rate = float(np.mean(p50[1:]))
    assert rms_err / mean_rate < 0.10


def test_returns_none_for_too_short_series() -> None:
    assert fit_p50_series([100.0, 80.0]) is None
    assert fit_p50_series([]) is None


def test_returns_none_for_all_null_series() -> None:
    assert fit_p50_series([None] * 12) is None


def test_handles_trailing_nulls() -> None:
    # 8 real points + 4 trailing nulls (well-count tapers off). Should
    # still produce a fit, ignoring the nulls.
    truth = {"qi": 500.0, "Di": 0.85, "b": 1.1, "Df": 0.08}
    p50 = _synthesize_p50(**truth, n_months=8) + [None] * 4
    r = fit_p50_series(p50)
    assert r is not None
    assert r["qi"] == pytest.approx(truth["qi"], rel=0.10)


def test_smoothed_rate_array_length_matches_input() -> None:
    truth = {"qi": 700.0, "Di": 1.0, "b": 1.3, "Df": 0.08}
    p50 = _synthesize_p50(**truth, n_months=24)
    r = fit_p50_series(p50)
    assert r is not None
    assert len(r["smoothed_rate"]) == 24


def test_basin_aware_df_flows_through_fit_and_moves_eur() -> None:
    # The cohort-Df wiring depends on df_terminal_per_year actually
    # reaching the imposed terminal decline and changing the EUR. Fit the
    # same P50 with Delaware's 0.08 (nominal, ~7.7% eff yr-1) and
    # Midland's shallower 0.06 (~5.8% eff): the fitted Df must track the
    # input, and the shallower terminal must lengthen the tail → higher
    # 50-yr EUR. Guards against a regression where the param is dropped.
    truth = {"qi": 600.0, "Di": 0.85, "b": 1.2, "Df": 0.08}
    p50 = _synthesize_p50(**truth, n_months=48)

    r_delaware = fit_p50_series(p50, df_terminal_per_year=0.08)
    r_midland = fit_p50_series(p50, df_terminal_per_year=0.06)
    assert r_delaware is not None and r_midland is not None

    assert r_delaware["Df"] == pytest.approx(0.08, abs=1e-6)
    assert r_midland["Df"] == pytest.approx(0.06, abs=1e-6)
    # Shallower terminal decline → longer-lived tail → larger EUR.
    assert r_midland["eur_per_unit"] > r_delaware["eur_per_unit"]


def _synthesize_ramp_arps(
    *, qo: float, qi: float, peak_index: int, Di: float, b: float, Df: float, n_months: int
) -> list[float]:
    """Synthesize a ramp-then-Arps series like a first-prod-aligned P50.
    Months 0..peak_index ramp linearly from qo → qi; months > peak_index
    follow modified-hyperbolic decline."""
    series: list[float] = []
    for i in range(min(peak_index + 1, n_months)):
        series.append(qo + (qi - qo) * (i / peak_index) if peak_index else qi)
    remaining = n_months - len(series)
    if remaining > 0:
        tail = _synthesize_p50(qi=qi, Di=Di, b=b, Df=Df, n_months=remaining)
        series.extend(tail)
    return series


def test_detects_ramp_in_first_prod_aligned_series() -> None:
    # Realistic Permian-shaped synthetic: low first-prod, peak at month 3,
    # then Arps decline. The fitter must NOT treat month 0 as peak.
    truth = {
        "qo": 80.0, "qi": 200.0, "peak_index": 3,
        "Di": 0.85, "b": 1.2, "Df": 0.08,
    }
    p50 = _synthesize_ramp_arps(**truth, n_months=36)
    r = fit_p50_series(p50)
    assert r is not None
    assert r["peak_index"] == truth["peak_index"]
    assert r["qo"] == pytest.approx(truth["qo"], rel=1e-6)
    # The fitted qi is bounded by Permian-typical config and the (Di, b)
    # degeneracy, but should land near the truth peak rate.
    assert r["qi"] == pytest.approx(truth["qi"], rel=0.15)
    # eur_per_unit includes the ramp prefix; ramp_eur should be > 0.
    assert r["ramp_eur"] > 0
    assert r["eur_per_unit"] == pytest.approx(
        r["ramp_eur"] + r["arps_eur"], rel=1e-9
    )
    # Smoothed curve should track the input series within ~10% RMS.
    rms = float(np.sqrt(np.mean(
        [(s - p) ** 2 for s, p in zip(r["smoothed_rate"], p50, strict=False)]
    )))
    mean_rate = float(np.mean(p50))
    assert rms / mean_rate < 0.10


def test_back_compatible_peak_aligned_series() -> None:
    # Pure post-peak series — should reduce to the old pure-Arps path:
    # peak_index == 0, qo == qi, ramp_eur == 0.
    truth = {"qi": 500.0, "Di": 0.9, "b": 1.1, "Df": 0.08}
    p50 = _synthesize_p50(**truth, n_months=36)
    r = fit_p50_series(p50)
    assert r is not None
    assert r["peak_index"] == 0
    assert r["qo"] == pytest.approx(p50[0], rel=1e-6)
    assert r["ramp_eur"] == 0.0
    assert r["eur_per_unit"] == pytest.approx(r["arps_eur"], rel=1e-9)
