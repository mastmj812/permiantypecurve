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
from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.fit import fit_rate_cum
from app.forecasting.peak_detection import PeakResult, detect_oil_peak

# Ramp math now lives in app.forecasting.ramp_arps so per-well
# forecasting and TC P50 fitting share one implementation. Re-exporting
# the names here keeps the existing import path stable for any callers.
from app.forecasting.ramp_arps import (  # noqa: F401 — re-exports
    build_ramp_arps_rate,
    compute_ramp_eur,
    evaluate_fit,
)
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
    except Exception as e:
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
