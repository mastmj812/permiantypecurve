"""Fit a ramp+Arps decline to the P50 of an aggregated type curve.

The P50 produced by `aggregate.py` is a raw cross-well monthly median —
it gets jagged as well_count tapers in the tail. Operator practice is
to publish the *fitted* smooth decline derived from that median, not
the median itself.

Real Permian wells aren't pure post-peak decliners: they ramp from a
first-prod rate `Qo` to a peak rate `qi` over the first 1–3 months,
then enter modified-hyperbolic Arps decline. With `first_prod_month`
type-curve alignment (the project default), the P50 series captures
that ramp, and a fit that assumes month 0 is already at peak (i.e. the
old `peak_index=0` shortcut) overshoots the early months.

This module:
  * Detects peak in the P50 series using the same rolling-max +
    tie-break-by-rate logic as per-well peak detection.
  * Fits modified-hyperbolic Arps to the *post-peak* slice via
    `fit_rate_cum` — that math path is unchanged from per-well.
  * Emits a `smoothed_rate` that's the concatenation of a linear ramp
    (Qo → qi over `peak_index` months) and the Arps tail.
  * Reports `eur_per_unit = ramp_eur + arps_eur` so the published EUR
    reflects the full ramp + decline.

The same ramp+Arps math is used by `/api/type-curves/preview` for
the manual-tweak UI, so auto-fit and manual paths can't drift.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.forecasting.cumulative import cum_modified_hyperbolic
from app.forecasting.eur import DAYS_PER_YEAR, compute_eur
from app.forecasting.fit import fit_rate_cum
from app.forecasting.peak_detection import PeakResult, detect_oil_peak
from app.forecasting.types import ForecastConfig

log = get_logger("type_curves.fit_p50")

# A synthetic reference date for the constructed dataframe — irrelevant
# to the fit since the fitter uses index-based time, but pandas needs
# real dates in the column.
_REF_DATE = date(2020, 1, 1)
_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0

# How many real (non-null, > 0) post-peak months we need to attempt an
# Arps fit. Below this the optimizer gets too few cum points to pin
# (qi, Di, b) — falls back to "no fit available".
_MIN_POST_PEAK_MONTHS: int = 6


def _make_monthly_df(p50: list[float | None]) -> pd.DataFrame:
    """Build a (prod_date, rate_calday_bopd, oil_bbl) frame the fitter
    can chew. Leading / mid-series nulls are zeroed (volume contribution
    is genuinely zero for those months); the caller is expected to have
    already trimmed trailing nulls."""
    rows: list[dict[str, Any]] = []
    d = pd.Timestamp(_REF_DATE)
    for v in p50:
        rate = float(v) if v is not None and np.isfinite(v) else 0.0
        rows.append(
            {
                "prod_date": d.date(),
                "rate_calday_bopd": rate,
                "oil_bbl": rate * _DAYS_PER_MONTH,
            }
        )
        d = d + pd.DateOffset(months=1)
    return pd.DataFrame(rows)


def build_ramp_arps_rate(
    *,
    n_months: int,
    qo: float,
    qi: float,
    peak_index: int,
    Di: float,
    b: float,
    Df: float,
) -> list[float]:
    """Evaluate the ramp+Arps model at each of n_months monthly grid points.

    Shape:
      * months [0, peak_index]:  linear ramp Qo → qi.
      * months (peak_index, n]:  modified-hyperbolic Arps starting at qi.

    Returns a Python list (the API serializes it directly).
    """
    if n_months <= 0:
        return []
    out: list[float] = []
    # Linear-ramp prefix. If peak_index == 0, this loop emits just qo for
    # month 0 and the Arps tail handles everything from month 1 forward —
    # which is the back-compatible pure-Arps case.
    if peak_index > 0:
        for i in range(min(peak_index + 1, n_months)):
            frac = i / peak_index
            out.append(qo + (qi - qo) * frac)
    else:
        out.append(float(qi))

    # Arps tail. The post-peak Arps grid uses t in years measured from
    # the peak month; month index `peak_index + k` (k = 1, 2, ...) sits
    # at t = k/12 in the Arps coordinate system.
    remaining = n_months - len(out)
    if remaining > 0:
        t_years = (np.arange(remaining, dtype=float) + 1.0) / 12.0
        tail = _eval_modified_hyperbolic_rate(t_years, qi=qi, Di=Di, b=b, Df=Df)
        out.extend(float(x) for x in tail)
    return out[:n_months]


def evaluate_fit(
    *,
    qi: float,
    Di: float,
    b: float,
    Df: float,
    qo: float,
    peak_index: int,
    n_months: int,
    horizon_years: float = 50.0,
    economic_limit: float = 5.0,
) -> dict[str, Any]:
    """Build a `fitted` dict from an explicit parameter set.

    Used by the `/api/type-curves/preview` endpoint (live manual tweak)
    and by the save-with-override path. The shape mirrors what
    `fit_p50_series` emits when it auto-fits, so the frontend treats
    auto and manual identically. `r2` / `rmse` are omitted (no data to
    score against here); callers that need them can still inspect
    the auto-fit those came with.
    """
    smoothed = build_ramp_arps_rate(
        n_months=n_months,
        qo=qo, qi=qi, peak_index=peak_index,
        Di=Di, b=b, Df=Df,
    )
    arps_eur = compute_eur(
        "modified_hyperbolic",
        {"qi": qi, "Di": Di, "b": b, "Df": Df},
        horizon_years=horizon_years,
        economic_limit=economic_limit,
    )
    ramp_eur = compute_ramp_eur(qo=qo, qi=qi, peak_index=peak_index)
    return {
        "model_type": "modified_hyperbolic",
        "qi": float(qi),
        "Di": float(Di),
        "b": float(b),
        "Df": float(Df),
        "qo": float(qo),
        "peak_index": int(peak_index),
        "ramp_eur": float(ramp_eur),
        "arps_eur": float(arps_eur),
        "eur_per_unit": float(ramp_eur + arps_eur),
        "smoothed_rate": smoothed,
        "manual_override": True,
    }


def compute_ramp_eur(*, qo: float, qi: float, peak_index: int) -> float:
    """Ramp-prefix EUR contribution: trapezoid area under Qo→qi over the
    ramp window, in the same units as `qi * DAYS_PER_MONTH` (BBL or MCF
    per normalization unit). Returns 0 when peak_index == 0."""
    if peak_index <= 0:
        return 0.0
    return ((qo + qi) / 2.0) * peak_index * _DAYS_PER_MONTH


def fit_p50_series(
    p50: list[float | None],
    *,
    df_terminal_per_year: float = 0.08,
    horizon_years: float = 50.0,
) -> dict[str, Any] | None:
    """Fit ramp+Arps modified-hyperbolic to the P50 series.

    Returns `None` if the series is too short or all-zero. Otherwise:

        {
          model_type, qi, Di, b, Df,   # Arps post-peak params
          qo, peak_index, ramp_eur,    # ramp prefix
          arps_eur, eur_per_unit,      # eur_per_unit = ramp + arps
          r2, rmse,                    # post-peak fit quality
          smoothed_rate: list[float],  # ramp + Arps evaluated at each input month
        }
    """
    # Trim trailing nulls — fitting flat-zero tail months would bias the
    # decline upward and the EUR downward. Leading / mid-series nulls
    # are still passed through.
    trimmed = list(p50)
    while trimmed and (trimmed[-1] is None or not np.isfinite(trimmed[-1]) or trimmed[-1] <= 0):
        trimmed.pop()
    real_values = [v for v in trimmed if v is not None and np.isfinite(v) and v > 0]
    if len(real_values) < _MIN_POST_PEAK_MONTHS:
        return None

    df = _make_monthly_df(trimmed)

    # Peak detection in the P50 series. The rolling-max + tie-break-by-
    # actual-rate logic in detect_oil_peak handles the partial-ramp
    # month-0 case correctly (per gotcha #11 in project memory).
    peak = detect_oil_peak(df)
    if peak is None:
        return None
    peak_index = peak.peak_index
    qo = float(real_values[0])  # initial rate at month 0 (first non-null)

    # Slice from the detected peak forward and feed to the same Arps
    # fitter that powers per-well forecasts. peak_index becomes 0 in
    # the sliced frame's coordinate system.
    df_post_peak = df.iloc[peak_index:].reset_index(drop=True)
    if len(df_post_peak) < _MIN_POST_PEAK_MONTHS:
        return None
    peak_for_fit = PeakResult(
        peak_month_date=peak.peak_month_date,
        peak_rate=peak.peak_rate,
        peak_index=0,
    )

    cfg = ForecastConfig(
        model_type="modified_hyperbolic",
        df_terminal_per_year=df_terminal_per_year,
        horizon_years=horizon_years,
        min_post_peak_months=3,
    )

    try:
        result = fit_rate_cum(
            df_post_peak,
            model_type="modified_hyperbolic",
            peak=peak_for_fit,
            stream="oil",
            config=cfg,
        )
    except Exception as e:  # noqa: BLE001 — fit failure shouldn't 500 the page
        log.warning("p50_fit_failed", error=str(e)[:200])
        return None

    qi = float(result.params["qi"])
    Di = float(result.params["Di"])
    b = float(result.params["b"])
    Df = float(result.params["Df"])

    smoothed = build_ramp_arps_rate(
        n_months=len(p50),
        qo=qo,
        qi=qi,
        peak_index=peak_index,
        Di=Di,
        b=b,
        Df=Df,
    )
    ramp_eur = compute_ramp_eur(qo=qo, qi=qi, peak_index=peak_index)
    arps_eur = float(result.eur)

    return {
        "model_type": result.model_type,
        "qi": qi,
        "Di": Di,
        "b": b,
        "Df": Df,
        "qo": qo,
        "peak_index": peak_index,
        "ramp_eur": ramp_eur,
        "arps_eur": arps_eur,
        # eur_per_unit is the total — ramp prefix + Arps tail — so the
        # display column and the saved EUR match the curve you see.
        "eur_per_unit": ramp_eur + arps_eur,
        "r2": float(result.fit_r2),
        "rmse": float(result.fit_rmse),
        "smoothed_rate": smoothed,
    }


def _eval_modified_hyperbolic_rate(
    t_years: np.ndarray,
    *,
    qi: float,
    Di: float,
    b: float,
    Df: float,
) -> np.ndarray:
    """Differentiate the closed-form cum to get rate at each time."""
    eps = 1.0 / (12.0 * 4.0)  # ~weekly step
    cum_lo = cum_modified_hyperbolic(np.maximum(t_years - eps, 0.0), qi, Di, b, Df)
    cum_hi = cum_modified_hyperbolic(t_years + eps, qi, Di, b, Df)
    rate_per_day = (cum_hi - cum_lo) / (2 * eps * DAYS_PER_YEAR)
    return np.maximum(rate_per_day, 0.0)
