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
    r = fit_rate_cum(df, model_type="arps_exponential", peak=peak, stream="oil", config=_UNCONSTRAINED)
    assert r.fit_r2 > 0.999
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.02)
    assert r.params["Di"] == pytest.approx(truth["Di"], rel=0.02)


def test_fit_recovers_hyperbolic_params() -> None:
    """Hyperbolic Arps with b > 1 has a known identifiability problem:
    over a finite data window, multiple (qi, Di, b) triples produce
    near-identical cum curves. The fit can land far from the "true"
    params while still scoring fit_r2 > 0.999. We assert the fit
    *quality* is excellent (the curve is reproduced) but only loose
    bounds on the params themselves — anything tighter would be
    asserting more than the math can deliver from 48 months of data.
    The exponential test above uses tight tolerances because it has
    only 2 free params, which ARE uniquely identifiable.
    """
    truth = {"qi": 800.0, "Di": 0.9, "b": 1.2}
    df = _synthetic_monthly(cum_fn=cum_hyperbolic, params=truth, months=48)
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(df, model_type="arps_hyperbolic", peak=peak, stream="oil", config=_UNCONSTRAINED)
    assert r.fit_r2 > 0.999
    # qi is anchored at t=0 so it's the most identifiable of the three.
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.10)
    # Di and b trade off — loose tolerances here are correct, not slop.
    assert r.params["Di"] == pytest.approx(truth["Di"], rel=0.25)
    assert r.params["b"] == pytest.approx(truth["b"], abs=0.4)


def test_fit_recovers_modified_hyperbolic_params() -> None:
    truth = {"qi": 700.0, "Di": 0.9, "b": 1.1, "Df": 0.08}
    df = _synthetic_monthly(
        cum_fn=lambda t, qi, Di, b, Df: cum_modified_hyperbolic(t, qi, Di, b, Df),
        params=truth,
        months=60,
    )
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(df, model_type="modified_hyperbolic", peak=peak, stream="oil", config=_UNCONSTRAINED)
    assert r.fit_r2 > 0.999
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.05)
    assert r.params["Di"] == pytest.approx(truth["Di"], rel=0.1)
    assert r.params["b"] == pytest.approx(truth["b"], abs=0.15)
    assert r.params["Df"] == pytest.approx(truth["Df"], abs=1e-6)


def test_rate_time_fit_also_recovers_hyperbolic_params() -> None:
    truth = {"qi": 800.0, "Di": 0.9, "b": 1.2}
    df = _synthetic_monthly(cum_fn=cum_hyperbolic, params=truth, months=48)
    peak = _peak_at_month_zero(df)
    r = fit_rate_time(df, model_type="arps_hyperbolic", peak=peak, stream="oil", config=_UNCONSTRAINED)
    assert r.fit_r2 > 0.99
    assert r.params["qi"] == pytest.approx(truth["qi"], rel=0.1)
    assert r.params["b"] == pytest.approx(truth["b"], abs=0.2)


def test_insufficient_history_flag_set_below_min_post_peak_months() -> None:
    truth = {"qi": 1000.0, "Di": 0.85}
    df = _synthetic_monthly(cum_fn=cum_exponential, params=truth, months=4)
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(df, model_type="arps_exponential", peak=peak, stream="oil", config=_UNCONSTRAINED)
    assert r.insufficient_history is True


def test_sufficient_history_flag_unset_at_six_months() -> None:
    truth = {"qi": 1000.0, "Di": 0.85}
    df = _synthetic_monthly(cum_fn=cum_exponential, params=truth, months=6)
    peak = _peak_at_month_zero(df)
    r = fit_rate_cum(df, model_type="arps_exponential", peak=peak, stream="oil", config=_UNCONSTRAINED)
    assert r.insufficient_history is False
