"""End-to-end fit tests with synthetic data of known parameters.

For each model we generate a noise-free monthly cumulative series from
the closed-form, then run `fit_rate_cum` and assert the recovered
qi/Di/b land within a tight tolerance of the truth.

These tests give us a fast signal when the fitter or cumulative formulas
drift — much faster than running the real-well CSVs through the pipeline.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.forecasting.cumulative import (
    cum_exponential,
    cum_hyperbolic,
    cum_modified_hyperbolic,
)
from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.fit import fit_rate_cum, fit_rate_time
from app.forecasting.peak_detection import PeakResult
from app.forecasting.types import ForecastConfig

# These tests verify the UNCONSTRAINED fitter recovers known params from
# clean data. Disable peak-anchoring of qi (a real-data heuristic that
# caps qi near the month-averaged peak — on a clean synthetic curve the
# instantaneous qi legitimately sits a few % above that monthly average).
_UNCONSTRAINED = ForecastConfig(qi_anchor_lo_frac=None, qi_anchor_hi_frac=None)


def _synthetic_monthly(
    *,
    cum_fn,  # one of cum_exponential, cum_hyperbolic, cum_modified_hyperbolic
    params: dict[str, float],
    months: int,
    rate_col: str = "rate_calday_bopd",
    vol_col: str = "oil_bbl",
    start: date = date(2023, 1, 1),
) -> pd.DataFrame:
    """Build a monthly frame whose cumulative matches the closed form exactly.

    Monthly volume = Q((i+1)/12) - Q(i/12); calday rate = vol / days_in_month.
    Caller specifies the closed-form cumulative function.
    """
    t = np.arange(months + 1, dtype=float) / 12.0
    cum = cum_fn(t, **params)
    monthly_vol = np.diff(cum)
    # Synthetic series → assume 30.4375 days/month.
    days_per_mo = DAYS_PER_YEAR / 12.0
    rate = monthly_vol / days_per_mo

    rows: list[dict] = []
    y, m = start.year, start.month
    for i in range(months):
        rows.append(
            {
                "prod_date": date(y, m, 1),
                vol_col: float(monthly_vol[i]),
                rate_col: float(rate[i]),
            }
        )
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return pd.DataFrame(rows)


def _peak_at_month_zero(df: pd.DataFrame, rate_col: str = "rate_calday_bopd") -> PeakResult:
    return PeakResult(
        peak_month_date=df.iloc[0]["prod_date"],
        peak_rate=float(df.iloc[0][rate_col]),
        peak_index=0,
    )


def test_fit_recovers_exponential_params() -> None:
    truth = {"qi": 1000.0, "Di": 0.85}
    df = _synthetic_monthly(cum_fn=cum_exponential, params=truth, months=36)
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(
        df, model_type="arps_exponential", peak=peak, stream="oil", config=_UNCONSTRAINED
    )
    assert r.fit_r2 > 0.999
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.02)
    assert r.params["Di"] == pytest.approx(truth["Di"], rel=0.02)


def test_fit_recovers_hyperbolic_params() -> None:
    """Noise-free hyperbolic data is recovered tightly, b included.

    This test used to carry loose tolerances (b abs=0.4) and a docstring
    blaming an "identifiability problem". The real cause was that
    ``cum_hyperbolic`` was b-insensitive inside |b-1| < 1e-4, so the fit
    never left its b = 1.0 start; a truth of 1.2 "passed" at 1.0. With
    the b-sensitive form the params come back to many digits.
    """
    truth = {"qi": 800.0, "Di": 0.9, "b": 1.2}
    df = _synthetic_monthly(cum_fn=cum_hyperbolic, params=truth, months=48)
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(
        df, model_type="arps_hyperbolic", peak=peak, stream="oil", config=_UNCONSTRAINED
    )
    assert r.fit_r2 > 0.999
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.01)
    assert r.params["Di"] == pytest.approx(truth["Di"], rel=0.01)
    assert r.params["b"] == pytest.approx(truth["b"], abs=0.01)


def test_fit_recovers_modified_hyperbolic_params() -> None:
    truth = {"qi": 700.0, "Di": 0.9, "b": 1.1, "Df": 0.08}
    df = _synthetic_monthly(
        cum_fn=lambda t, qi, Di, b, Df: cum_modified_hyperbolic(t, qi, Di, b, Df),
        params=truth,
        months=60,
    )
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(
        df, model_type="modified_hyperbolic", peak=peak, stream="oil", config=_UNCONSTRAINED
    )
    assert r.fit_r2 > 0.999
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.05)
    assert r.params["Di"] == pytest.approx(truth["Di"], rel=0.01)
    assert r.params["b"] == pytest.approx(truth["b"], abs=0.01)
    assert r.params["Df"] == pytest.approx(truth["Df"], abs=1e-6)


def test_cum_fit_moves_b_off_its_unit_start() -> None:
    """Regression for the frozen-b defect. Every fit starts at B_P0 = 1.0;
    on a b = 0.92 well (inside the production [0.9, 1.2] bounds) the cum
    fit must actually walk b down to the truth. Nominal Di 2.4/yr is a
    ~71% effective year-1 decline — a typical Permian oil well."""
    from app.forecasting.fit import B_P0

    assert B_P0 == 1.0  # the start that used to trap the optimizer
    truth = {"qi": 900.0, "Di": 2.4, "b": 0.92, "Df": 0.08}
    df = _synthetic_monthly(
        cum_fn=lambda t, qi, Di, b, Df: cum_modified_hyperbolic(t, qi, Di, b, Df),
        params=truth,
        months=36,
    )
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(
        df, model_type="modified_hyperbolic", peak=peak, stream="oil", config=_UNCONSTRAINED
    )
    assert abs(r.params["b"] - 1.0) > 0.05, "b never left its 1.0 start"
    assert r.params["b"] == pytest.approx(truth["b"], abs=0.005)
    assert r.params["Di"] == pytest.approx(truth["Di"], rel=0.01)
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.01)


def test_cum_hyperbolic_is_smooth_and_b_sensitive_through_unity() -> None:
    t = np.array([0.25, 1.0, 5.0, 30.0])
    qi, Di = 800.0, 2.5

    # b = 1 is exactly the harmonic closed form.
    harmonic = qi / Di * np.log1p(Di * t) * DAYS_PER_YEAR
    np.testing.assert_allclose(cum_hyperbolic(t, qi, Di, 1.0), harmonic, rtol=1e-13)

    # Away from b = 1 it still equals the textbook expression.
    for b in (0.5, 0.9, 0.99, 1.01, 1.2, 1.6):
        textbook = qi / (Di * (1 - b)) * (1 - np.power(1 + b * Di * t, 1 - 1 / b)) * DAYS_PER_YEAR
        np.testing.assert_allclose(cum_hyperbolic(t, qi, Di, b), textbook, rtol=1e-9)

    # dQ/db at b = 1 by the SAME forward step curve_fit takes (~1.5e-8)
    # must match a wide central difference — it was identically 0 before.
    h = 1.5e-8
    forward = (cum_hyperbolic(t, qi, Di, 1.0 + h) - cum_hyperbolic(t, qi, Di, 1.0)) / h
    central = (cum_hyperbolic(t, qi, Di, 1.001) - cum_hyperbolic(t, qi, Di, 0.999)) / 2e-3
    assert np.all(forward > 0)
    np.testing.assert_allclose(forward, central, rtol=1e-3)

    # No seam where the old harmonic hand-off band ended.
    lo, hi = cum_hyperbolic(t, qi, Di, 1.0 + 0.9999e-4), cum_hyperbolic(t, qi, Di, 1.0 + 1.0001e-4)
    np.testing.assert_allclose(lo, hi, rtol=1e-7)


def test_rate_time_fit_also_recovers_hyperbolic_params() -> None:
    truth = {"qi": 800.0, "Di": 0.9, "b": 1.2}
    df = _synthetic_monthly(cum_fn=cum_hyperbolic, params=truth, months=48)
    peak = _peak_at_month_zero(df)
    r = fit_rate_time(
        df, model_type="arps_hyperbolic", peak=peak, stream="oil", config=_UNCONSTRAINED
    )
    assert r.fit_r2 > 0.99
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.1)
    assert r.params["b"] == pytest.approx(truth["b"], abs=0.2)


def test_insufficient_history_flag_set_below_min_post_peak_months() -> None:
    truth = {"qi": 1000.0, "Di": 0.85}
    df = _synthetic_monthly(cum_fn=cum_exponential, params=truth, months=4)
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(
        df, model_type="arps_exponential", peak=peak, stream="oil", config=_UNCONSTRAINED
    )
    assert r.insufficient_history is True


def test_sufficient_history_flag_unset_at_six_months() -> None:
    truth = {"qi": 1000.0, "Di": 0.85}
    df = _synthetic_monthly(cum_fn=cum_exponential, params=truth, months=6)
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(
        df, model_type="arps_exponential", peak=peak, stream="oil", config=_UNCONSTRAINED
    )
    assert r.insufficient_history is False
