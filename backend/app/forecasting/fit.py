"""Decline-curve fitters.

Two fit methods, both produce a `ForecastResult`:

  * `fit_rate_cum` — default. Integrates the rate model to closed-form
    cumulative production, then NLS-fits cumulative-vs-time peak-month
    forward. Less noise-sensitive than rate-vs-time because cum is
    monotone and integrates over operational hiccups.

  * `fit_rate_time` — per-well override. NLS on calday-rate vs time.
    Useful when a well had a workover that smears the cum into a step.

Bounds and initial guesses come from the brief, scaled by peak rate.

Volume / cumulative units: BBL (or MCF for gas). Rate units: BOPD / MCFD
/ BWPD. Time axis: years since peak month, with each monthly data point
placed at the end-of-month boundary (t_i = (i+1)/12).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.optimize import curve_fit

from app.core.logging import get_logger
from app.forecasting.cumulative import (
    cum_exponential,
    cum_harmonic,
    cum_hyperbolic,
    cum_modified_hyperbolic,
)
from app.forecasting.eur import compute_eur
from app.forecasting.models import (
    arps_exponential,
    arps_harmonic,
    arps_hyperbolic,
    modified_hyperbolic,
)
from app.forecasting.peak_detection import PeakResult
from app.forecasting.types import ForecastConfig, ForecastResult

log = get_logger("forecasting.fit")

# ---------------- Permian-typical fit bounds ----------------
# All Di values here are *nominal* per-year (Arps original convention) —
# the value that appears directly in q(t) = qi / (1 + b*Di*t)^(1/b).
# Effective annual decline (the percent rate-drop in year 1) is shown
# alongside in the UI via app.forecasting.metrics.effective_decline_first_year.
#
# Why these specific bounds:
#   * Di_lo = 0.5  — squeezed from 0.3 once we saw the rate-time fallback
#                    occasionally land near 0.3 on noisy wells. A producing
#                    Permian unconventional declining slower than ~33%
#                    effective in year 1 (Di_nom=0.5 at b=1) is implausible.
#   * Di_hi = 4.0  — squeezed from 5.0 for the same reason. ~78% effective
#                    at b=1 covers the steepest Permian wells we've seen;
#                    fits that want >78% effective signal a degenerate
#                    short-history fit, not a real well.
#   * b in [0.9, 1.2] — Wolfcamp-typical petrophysical range. Started at
#                       [0, 2] (brief), tightened to [0.7, 1.5] in step 4
#                       to kill underdetermined corners, then tightened
#                       further to [0.9, 1.2] once the rate-time fallback
#                       (fit_with_fallback) started exposing wells whose
#                       free-b drifted wider than petrophysically plausible.
#
# Other plays (Eagle Ford, Bakken, conventional) should override these via
# ForecastConfig once we make them configurable — for now they're hardcoded
# Permian defaults.
DI_NOMINAL_LO_PER_YEAR: float = 0.5
DI_NOMINAL_HI_PER_YEAR: float = 4.0
B_LO: float = 0.9
B_HI: float = 1.2

# Initial guess: basin-typical Wolfcamp values, centered in the bounds.
DI_NOMINAL_P0: float = 1.0
B_P0: float = 1.0

# How close to a bound is "pinned"? 2% margin on the upper/lower side of
# each bound. Used to flag fits where the optimizer was constrained, so
# the UI can surface a badge for engineer review.
BOUND_TOLERANCE_PCT: float = 0.02


def detect_at_bound(
    *,
    qi: float,
    di: float,
    b: float | None,
    peak_rate: float,
) -> tuple[bool, str | None]:
    """Return (any_at_bound, comma-joined note). Caller decides UI treatment."""
    notes: list[str] = []
    qi_max = max(10.0 * peak_rate, 1.0)
    if qi > 0 and qi_max > 0 and (qi_max - qi) / qi_max < BOUND_TOLERANCE_PCT:
        notes.append(f"qi at upper bound ({qi_max:.0f})")
    if di < DI_NOMINAL_LO_PER_YEAR * (1 + BOUND_TOLERANCE_PCT):
        notes.append(f"Di at lower bound ({DI_NOMINAL_LO_PER_YEAR:.1f})")
    if di > DI_NOMINAL_HI_PER_YEAR * (1 - BOUND_TOLERANCE_PCT):
        notes.append(f"Di at upper bound ({DI_NOMINAL_HI_PER_YEAR:.1f})")
    if b is not None:
        if b < B_LO + BOUND_TOLERANCE_PCT:
            notes.append(f"b at lower bound ({B_LO:.1f})")
        if b > B_HI - BOUND_TOLERANCE_PCT:
            notes.append(f"b at upper bound ({B_HI:.1f})")
    return (bool(notes), "; ".join(notes) if notes else None)

# Volume + rate columns per stream — match production_monthly schema.
STREAM_RATE_COLUMN = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_mcfd",
    "water": "rate_calday_bwpd",
}
STREAM_VOLUME_COLUMN = {
    "oil": "oil_bbl",
    "gas": "gas_mcf",
    "water": "water_bbl",
}
STREAM_ECON_LIMIT_FIELD = {
    "oil": "economic_limit_bopd",
    "gas": "economic_limit_mcfd",
    "water": "economic_limit_bwpd",
}

# Models that take the same parameter set as modified_hyperbolic — Df is
# fixed during the fit (per brief; user can override at the call site).
MODEL_FREE_PARAMS = {
    "arps_exponential": ("qi", "Di"),
    "arps_hyperbolic": ("qi", "Di", "b"),
    "arps_harmonic": ("qi", "Di"),
    "modified_hyperbolic": ("qi", "Di", "b"),  # Df fixed
}


# Downtime-filter heuristic. A post-peak month is flagged as downtime
# (and dropped from the fit) when its calday rate is below this fraction
# of the local rolling max. Centered 7-month window is wide enough to
# span typical 1–2 month offline bouts AND include normal-producing
# months on either side, so the local max stays anchored to the real
# decline trend rather than getting pulled down by the dip itself.
#
# Why this beats `producing_days`: Enverus reports `producing_days`
# inconsistently — some operators report it accurately, others fill it
# with calendar-day counts, others zero it out. The calday rate
# (`oil_bbl / 30.4`) lies under all of those reporting patterns
# because a half-month of production at normal flow looks identical to
# a full month at half flow. Comparing each month to its neighbors via
# rolling-max sidesteps the data-quality issue entirely.
_DOWNTIME_WINDOW: int = 7
_DOWNTIME_THRESHOLD: float = 0.30


def _flag_downtime(rates: pd.Series) -> pd.Series:
    """Return a boolean Series — True where the month looks like downtime.

    Computed against the centered rolling max so single-month and
    multi-month dips both surface as long as the surrounding window
    still contains at least one normal-flow month. min_periods=1 keeps
    the edges from going NaN; the first/last months use whatever
    neighbors exist."""
    local_max = rates.rolling(
        window=_DOWNTIME_WINDOW, center=True, min_periods=1
    ).max()
    # Floor the threshold at 1.0 BOPD-equivalent so all-zero late-tail
    # months don't accidentally flag as downtime against a near-zero
    # local max. Real downtime months stand out against a non-trivial
    # local max from a producing neighbor.
    threshold = (_DOWNTIME_THRESHOLD * local_max).clip(lower=1.0)
    return rates < threshold


def _post_peak_slice(
    monthly_df: pd.DataFrame,
    peak: PeakResult,
    rate_col: str,
    vol_col: str,
    *,
    filter_downtime: bool = True,
) -> tuple[pd.DataFrame, float]:
    """Return (rows-from-peak-forward-after-downtime-filter, downtime_ratio).

    `cum_vol` is computed BEFORE filtering, so the remaining months
    still carry the true cumulative volume as of their calendar month —
    the cum-fit doesn't lose the (small) production from the dropped
    downtime months.

    `downtime_ratio` is the fraction of post-peak months that were
    flagged. Persisted on the Forecast row so the Review grid can
    surface noisy wells.
    """
    df = monthly_df.sort_values("prod_date").reset_index(drop=True)
    df = df.iloc[peak.peak_index :].copy()
    # End-of-month timestamps as years since peak month start.
    # The k-th post-peak month (0-indexed) finishes at t = (k+1)/12.
    df["t_years"] = (np.arange(len(df), dtype=float) + 1.0) / 12.0
    df["cum_vol"] = df[vol_col].astype(float).cumsum()
    df["rate"] = df[rate_col].astype(float)

    if not filter_downtime or len(df) == 0:
        return df, 0.0

    downtime_mask = _flag_downtime(df["rate"])
    ratio = float(downtime_mask.sum()) / float(len(df))
    if downtime_mask.any():
        df = df.loc[~downtime_mask].reset_index(drop=True)
    return df, ratio


def _r_squared(actual: NDArray[np.float64], predicted: NDArray[np.float64]) -> float:
    ss_res = float(np.sum((actual - predicted) ** 2))
    mean_actual = float(np.mean(actual))
    ss_tot = float(np.sum((actual - mean_actual) ** 2))
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return 1.0 - ss_res / ss_tot


def _rmse(actual: NDArray[np.float64], predicted: NDArray[np.float64]) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def _cum_callable(
    model_type: str, df_terminal: float
) -> Callable[..., NDArray[np.float64]]:
    """Curry the Df out of modified_hyperbolic so curve_fit only varies the
    free parameters listed in MODEL_FREE_PARAMS."""
    if model_type == "arps_exponential":
        return cum_exponential
    if model_type == "arps_hyperbolic":
        return cum_hyperbolic
    if model_type == "arps_harmonic":
        return cum_harmonic
    if model_type == "modified_hyperbolic":
        return lambda t, qi, Di, b: cum_modified_hyperbolic(t, qi, Di, b, df_terminal)
    raise ValueError(f"unsupported model_type: {model_type}")


def _rate_callable(
    model_type: str, df_terminal: float
) -> Callable[..., NDArray[np.float64]]:
    if model_type == "arps_exponential":
        return arps_exponential
    if model_type == "arps_hyperbolic":
        return arps_hyperbolic
    if model_type == "arps_harmonic":
        return arps_harmonic
    if model_type == "modified_hyperbolic":
        return lambda t, qi, Di, b: modified_hyperbolic(t, qi, Di, b, df_terminal)
    raise ValueError(f"unsupported model_type: {model_type}")


def _bounds_and_p0(
    model_type: str, peak_rate: float
) -> tuple[tuple[list[float], list[float]], list[float]]:
    """Return ((lower, upper), initial-guess) for scipy.optimize.curve_fit.

    All bounds and starting values are Permian-typical (see module-level
    constants). Hitting a bound during the fit is a signal that the data
    is underdetermined — the orchestrator can flag it on the row.
    """
    qi_max = max(10.0 * peak_rate, 1.0)
    qi_lo, qi_hi = 0.0, qi_max
    di_lo, di_hi = DI_NOMINAL_LO_PER_YEAR, DI_NOMINAL_HI_PER_YEAR
    b_lo, b_hi = B_LO, B_HI

    # Clamp starting qi to the data so curve_fit doesn't start outside bounds.
    qi_p0 = float(np.clip(peak_rate, qi_lo + 1e-6, qi_hi))

    if model_type in ("arps_exponential", "arps_harmonic"):
        return (([qi_lo, di_lo], [qi_hi, di_hi]), [qi_p0, DI_NOMINAL_P0])
    if model_type in ("arps_hyperbolic", "modified_hyperbolic"):
        return (
            ([qi_lo, di_lo, b_lo], [qi_hi, di_hi, b_hi]),
            [qi_p0, DI_NOMINAL_P0, B_P0],
        )
    raise ValueError(f"unsupported model_type: {model_type}")


def _params_to_dict(
    model_type: str, fit_params: NDArray[np.float64], df_terminal: float
) -> dict[str, float]:
    if model_type == "arps_exponential":
        return {"qi": float(fit_params[0]), "Di": float(fit_params[1])}
    if model_type == "arps_harmonic":
        return {"qi": float(fit_params[0]), "Di": float(fit_params[1])}
    if model_type == "arps_hyperbolic":
        return {
            "qi": float(fit_params[0]),
            "Di": float(fit_params[1]),
            "b": float(fit_params[2]),
        }
    if model_type == "modified_hyperbolic":
        return {
            "qi": float(fit_params[0]),
            "Di": float(fit_params[1]),
            "b": float(fit_params[2]),
            "Df": float(df_terminal),
        }
    raise ValueError(model_type)


def _fit_core(
    df: pd.DataFrame,
    *,
    target_col: str,
    func: Callable[..., NDArray[np.float64]],
    bounds: tuple[list[float], list[float]],
    p0: list[float],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Run scipy.optimize.curve_fit on the prepared dataframe.

    Returns (params, predicted_values). Raises on convergence failure —
    the caller should wrap and surface a clean message.
    """
    t = df["t_years"].to_numpy(dtype=float)
    y = df[target_col].to_numpy(dtype=float)
    popt, _pcov = curve_fit(
        func, t, y, p0=p0, bounds=bounds, maxfev=10_000
    )
    return popt, func(t, *popt)


def _build_result(
    *,
    model_type: str,
    fit_method: str,
    popt: NDArray[np.float64],
    predicted: NDArray[np.float64],
    actual: NDArray[np.float64],
    peak: PeakResult,
    config: ForecastConfig,
    stream: str,
    n_points_fit: int,
    insufficient_history: bool,
    downtime_ratio: float = 0.0,
) -> ForecastResult:
    params = _params_to_dict(model_type, popt, config.df_terminal_per_year)
    econ_limit = getattr(config, STREAM_ECON_LIMIT_FIELD[stream])
    eur = compute_eur(
        model_type,
        params,
        horizon_years=config.horizon_years,
        economic_limit=econ_limit,
    )

    return ForecastResult(
        model_type=model_type,
        params=params,
        qi=params["qi"],
        di_initial=params.get("Di"),
        b=params.get("b"),
        df_terminal=params.get("Df"),
        eur=eur,
        peak_month_date=peak.peak_month_date,
        peak_rate=peak.peak_rate,
        fit_method=fit_method,
        fit_r2=_r_squared(actual, predicted),
        fit_rmse=_rmse(actual, predicted),
        n_points_fit=n_points_fit,
        insufficient_history=insufficient_history,
        downtime_ratio=downtime_ratio,
    )


def fit_rate_cum(
    monthly_df: pd.DataFrame,
    *,
    model_type: str = "modified_hyperbolic",
    peak: PeakResult,
    stream: str = "oil",
    config: ForecastConfig | None = None,
) -> ForecastResult:
    """Default fit: cumulative-vs-time NLS, peak-month forward."""
    cfg = config or ForecastConfig()
    rate_col = STREAM_RATE_COLUMN[stream]
    vol_col = STREAM_VOLUME_COLUMN[stream]
    df, downtime_ratio = _post_peak_slice(monthly_df, peak, rate_col, vol_col)
    insufficient = len(df) < cfg.min_post_peak_months

    func = _cum_callable(model_type, cfg.df_terminal_per_year)
    bounds, p0 = _bounds_and_p0(model_type, peak.peak_rate)

    popt, predicted = _fit_core(
        df, target_col="cum_vol", func=func, bounds=bounds, p0=p0
    )
    actual = df["cum_vol"].to_numpy(dtype=float)
    return _build_result(
        model_type=model_type,
        fit_method="rate_cum",
        popt=popt,
        predicted=predicted,
        actual=actual,
        peak=peak,
        config=cfg,
        stream=stream,
        n_points_fit=len(df),
        insufficient_history=insufficient,
        downtime_ratio=downtime_ratio,
    )


def fit_rate_time(
    monthly_df: pd.DataFrame,
    *,
    model_type: str = "modified_hyperbolic",
    peak: PeakResult,
    stream: str = "oil",
    config: ForecastConfig | None = None,
) -> ForecastResult:
    """Per-well override: instantaneous rate vs time NLS."""
    cfg = config or ForecastConfig()
    rate_col = STREAM_RATE_COLUMN[stream]
    vol_col = STREAM_VOLUME_COLUMN[stream]
    df, downtime_ratio = _post_peak_slice(monthly_df, peak, rate_col, vol_col)
    insufficient = len(df) < cfg.min_post_peak_months

    func = _rate_callable(model_type, cfg.df_terminal_per_year)
    bounds, p0 = _bounds_and_p0(model_type, peak.peak_rate)

    popt, predicted = _fit_core(
        df, target_col="rate", func=func, bounds=bounds, p0=p0
    )
    actual = df["rate"].to_numpy(dtype=float)
    return _build_result(
        model_type=model_type,
        fit_method="rate_time",
        popt=popt,
        predicted=predicted,
        actual=actual,
        peak=peak,
        config=cfg,
        stream=stream,
        n_points_fit=len(df),
        insufficient_history=insufficient,
        downtime_ratio=downtime_ratio,
    )


# Acceptance floor for the rate-time fallback's R². Cum R² and rate R²
# aren't directly comparable (different targets, different noise floors),
# so we use an absolute floor here rather than comparing to primary.fit_r2.
# 0.3 is generous — even a noisy rate-time fit that clears the Di bound
# is a better engineer-defensible default than a cum fit pinned at 5.0.
_FALLBACK_MIN_R2: float = 0.3


def _di_at_bound(di: float | None) -> bool:
    if di is None:
        return False
    return (
        di < DI_NOMINAL_LO_PER_YEAR * (1 + BOUND_TOLERANCE_PCT)
        or di > DI_NOMINAL_HI_PER_YEAR * (1 - BOUND_TOLERANCE_PCT)
    )


def fit_with_fallback(
    monthly_df: pd.DataFrame,
    *,
    model_type: str = "modified_hyperbolic",
    peak: PeakResult,
    stream: str = "oil",
    config: ForecastConfig | None = None,
) -> ForecastResult:
    """Default `rate_cum` fit with a `rate_time` retry on Di-at-bound.

    Why: `fit_rate_cum` integrates the rate model to a closed-form
    cumulative, then NLS-fits cum-vs-time. The integral has very low
    Jacobian sensitivity to b — small b changes produce small cum
    differences over typical post-peak windows — so the optimizer
    often leaves b near its initial guess (1.0) and absorbs the misfit
    into Di. On wells where the "true" b ≠ 1, Di then pins at the
    bound (0.3 or 5.0) and the fit is flagged. Rate-vs-time is noisier
    but more constraining on b, so it can produce a different
    (Di, b) pair that doesn't pin.

    Trigger: cum-fit's Di hits the lo/hi bound. Other bound hits
    (qi-at-upper, b-at-bound) are not retried — leave the engineer the
    original flag.

    Accept fallback when: rate-time Di is NOT at a bound AND
    rate-time R² ≥ `_FALLBACK_MIN_R2`.

    The adopted result is tagged `fit_method="rate_time_fallback"` so
    the engineer can see in the grid which wells were rescued.
    """
    primary = fit_rate_cum(
        monthly_df,
        model_type=model_type,
        peak=peak,
        stream=stream,
        config=config,
    )
    if not _di_at_bound(primary.di_initial):
        return primary
    try:
        fallback = fit_rate_time(
            monthly_df,
            model_type=model_type,
            peak=peak,
            stream=stream,
            config=config,
        )
    except Exception as e:  # noqa: BLE001 — fallback is best-effort
        log.info(
            "fallback_fit_failed",
            stream=stream,
            err=str(e),
            primary_di=primary.di_initial,
        )
        return primary
    if _di_at_bound(fallback.di_initial) or fallback.fit_r2 < _FALLBACK_MIN_R2:
        return primary
    log.info(
        "fallback_adopted",
        stream=stream,
        primary_di=primary.di_initial,
        fallback_di=fallback.di_initial,
        fallback_b=fallback.b,
        fallback_r2=fallback.fit_r2,
    )
    note = (
        f"cum fit pinned Di={primary.di_initial:.2f}; "
        f"rate-time fallback Di={fallback.di_initial:.2f}, "
        f"b={(fallback.b if fallback.b is not None else float('nan')):.2f}"
    )
    return replace(
        fallback,
        fit_method="rate_time_fallback",
        notes=(note + ("\n" + fallback.notes if fallback.notes else "")),
    )
