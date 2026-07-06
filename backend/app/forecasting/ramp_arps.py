"""Linear-ramp + modified-hyperbolic Arps decline.

Real Permian wells aren't pure post-peak decliners: they ramp from a
first-prod rate ``qo`` to a peak rate ``qi`` over the first 1-3 months,
then enter modified-hyperbolic Arps decline. This module centralizes
the math so the per-well forecast (`/api/forecasts/{api10}/curves` +
`/preview`), the per-well EUR persisted at fit time, the cohort-
transfer write, and the TC P50 fit all use one implementation.

Public entry points:

  * ``evaluate_well_rate(qo, peak_index_months, qi, Di, b, Df, t_years)``
      — sample the model at arbitrary years-since-first-prod points
      (linspace, monthly grid, anything). Falls back to pure Arps when
      ``qo`` or ``peak_index_months`` is None — covers forecasts that
      pre-date the ramp columns and haven't been re-fit.

  * ``build_ramp_arps_rate(...)`` — monthly grid evaluator used by the
      TC P50 fit (kept as-is for back-compat with the saved-curve
      JSONB shape).

  * ``compute_ramp_eur(qo, qi, peak_index)`` — trapezoid area under
      the ramp, in BBL (or MCF) per normalization unit. Add to the
      Arps tail's EUR for the total well EUR.

  * ``evaluate_fit(...)`` — builds the ``fitted`` dict the TC preview
      endpoint returns; centralizes the (ramp, Arps, totals) shape.

Lifted from ``type_curves/fit_p50.py`` to break the directional
dependency — the per-well forecasting layer now needs the same math
the TC layer was using. The TC module re-imports from here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.forecasting.eur import DAYS_PER_YEAR, compute_eur
from app.forecasting.models import modified_hyperbolic

_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0

# Monthly grid length for the display-convention EUR: 600 months = the
# 50-yr default horizon. Matches the frontend's FULL_FORECAST_N_MONTHS.
DISPLAY_EUR_N_MONTHS: int = 600


# ---------------------------------------------------------------------
# Rate evaluation
# ---------------------------------------------------------------------


def evaluate_well_rate(
    *,
    qo: float | None,
    peak_index_months: int | None,
    qi: float,
    Di: float,
    b: float,
    Df: float,
    t_years: NDArray[np.float64],
) -> NDArray[np.float64]:
    """Sample the well's rate at the given years-since-first-prod array.

    The model is a piecewise function:

      * ``t < peak_index_months/12`` — linear ramp qo + (qi - qo) * (12*t / peak_index_months).
      * ``t >= peak_index_months/12`` — modified-hyperbolic Arps in
        post-peak time (subtract peak_t from t before evaluating).

    Falls back to **pure modified-hyperbolic anchored at t=0** when
    either ``qo`` or ``peak_index_months`` is None — the back-compat
    path for forecasts persisted before the ramp columns existed and
    not yet re-fit. ``peak_index_months == 0`` (peak at first prod)
    also degenerates to pure Arps with no ramp prefix.
    """
    t = np.asarray(t_years, dtype=float)
    # Back-compat: missing ramp params → straight Arps from t=0.
    if qo is None or peak_index_months is None or peak_index_months <= 0:
        return _eval_modified_hyperbolic_rate(t, qi=qi, Di=Di, b=b, Df=Df)

    peak_t = peak_index_months / 12.0
    out = np.empty_like(t)
    ramp_mask = t <= peak_t
    # Linear interpolation qo → qi over [0, peak_t]. At t == peak_t the
    # ramp value is exactly qi, so the seam with the Arps tail is
    # continuous.
    out[ramp_mask] = qo + (qi - qo) * (t[ramp_mask] / peak_t)
    # Post-peak: Arps in coordinates measured from peak. arps_t starts
    # at 0 right at the seam → rate equals qi → matches the ramp's
    # endpoint exactly.
    arps_t = t[~ramp_mask] - peak_t
    out[~ramp_mask] = _eval_modified_hyperbolic_rate(
        arps_t, qi=qi, Di=Di, b=b, Df=Df
    )
    return np.maximum(out, 0.0)


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

    Kept with the same signature the TC fit_p50 code uses so the saved-
    curve JSONB shape doesn't shift. Internally just calls
    ``evaluate_well_rate`` with a monthly grid.
    """
    if n_months <= 0:
        return []
    t_years = np.arange(n_months, dtype=float) / 12.0
    rates = evaluate_well_rate(
        qo=qo,
        peak_index_months=peak_index,
        qi=qi,
        Di=Di,
        b=b,
        Df=Df,
        t_years=t_years,
    )
    return [float(x) for x in rates]


# ---------------------------------------------------------------------
# EUR
# ---------------------------------------------------------------------


def compute_ramp_eur(*, qo: float, qi: float, peak_index: int) -> float:
    """Ramp-prefix EUR contribution: trapezoid area under qo→qi over the
    ramp window. Same volume units as ``qi * DAYS_PER_MONTH`` — BBL or
    MCF per normalization unit. Returns 0 when peak_index <= 0."""
    if peak_index <= 0:
        return 0.0
    return ((qo + qi) / 2.0) * peak_index * _DAYS_PER_MONTH


def model_cum_at_t(
    *,
    qo: float | None,
    peak_index_months: int | None,
    qi: float,
    Di: float,
    b: float,
    Df: float,
    t_years: float,
) -> float:
    """Cumulative production at time ``t_years`` since first prod.

    Piecewise:

      * ``t <= peak_t`` — trapezoid under the partial ramp from 0 to t.
        Rate at t is ``qo + (qi - qo) * (t / peak_t)``; the area is the
        average of that and qo times t, scaled by DAYS_PER_YEAR.
      * ``t >  peak_t`` — full ramp area + Arps integral over the
        post-peak horizon ``t - peak_t``.

    Back-compat: with ``qo`` / ``peak_index_months`` missing (or
    ``peak_index_months == 0``), this degenerates to ``compute_eur``
    on the Arps params with horizon == t_years — matches the pure-
    Arps anchor convention.

    Used to split the model EUR at end-of-history when computing
    ``remaining_per_model`` (= full_eur - model_cum_at_endofhistory)
    and the Method-1 displayed EUR (= actual_cum_to_date + remaining).
    """
    if t_years <= 0:
        return 0.0
    arps_params: dict[str, float] = {"qi": qi, "Di": Di, "b": b, "Df": Df}
    if qo is None or peak_index_months is None or peak_index_months <= 0:
        return compute_eur(
            "modified_hyperbolic", arps_params, horizon_years=t_years
        )
    peak_t = peak_index_months / 12.0
    if t_years <= peak_t:
        rate_at_t = qo + (qi - qo) * (t_years / peak_t)
        return ((qo + rate_at_t) / 2.0) * t_years * DAYS_PER_YEAR
    ramp_eur = compute_ramp_eur(qo=qo, qi=qi, peak_index=peak_index_months)
    arps_cum = compute_eur(
        "modified_hyperbolic", arps_params, horizon_years=t_years - peak_t
    )
    return ramp_eur + arps_cum


def compute_total_eur(
    *,
    model_type: str,
    params: dict[str, Any],
    horizon_years: float = 50.0,
    economic_limit: float = 0.0,
) -> float:
    """ramp_eur + arps_eur over the well's total ``horizon_years`` of
    producing life. The ramp consumes ``peak_index_months / 12`` years
    up front; the Arps tail is then integrated over the REMAINING
    ``horizon_years - peak_t`` years so the two pieces sum to exactly
    the desired horizon. Without this subtraction the Arps integral
    overlaps the ramp window and the total exceeds ``horizon_years`` of
    real producing time by ``peak_t`` years — a ~0.02% bias on a
    typical 3-month ramp with a 50-yr horizon, harmless in absolute
    terms but it desynchronizes ``remaining = total - model_cum_at_t``
    at t == horizon (the remaining should be zero, not a few hundred
    BBL of phantom tail).

    Drops to plain ``compute_eur`` when qo or peak_index_months is
    missing from ``params`` (forecasts persisted before the ramp
    columns existed and not yet re-fit).
    """
    qo = params.get("qo")
    peak_index_months = params.get("peak_index_months")
    qi = params.get("qi")
    if qo is None or peak_index_months is None or qi is None or peak_index_months <= 0:
        return compute_eur(
            model_type, params, horizon_years=horizon_years, economic_limit=economic_limit
        )
    peak_t = int(peak_index_months) / 12.0
    arps_horizon = max(0.0, horizon_years - peak_t)
    arps_eur = compute_eur(
        model_type, params, horizon_years=arps_horizon, economic_limit=economic_limit
    )
    return (
        compute_ramp_eur(qo=float(qo), qi=float(qi), peak_index=int(peak_index_months))
        + arps_eur
    )


def trapezoid_eur(rates: Sequence[float | None]) -> float:
    """Trapezoid integral of a monthly rate grid → volume units.

    ``rates[i]`` = q(t = i months). Month i's volume is the average of
    its two endpoint rates × the month length (DAYS_PER_YEAR / 12); the
    final month is flat-extrapolated. This is THE display-side
    integration rule: the deal xlsx/pptx well rows, the TC well-stats
    endpoint, the per-percentile export EURs, and the frontend's
    ``arps.ts::eurFromArpsParams`` (its TS mirror) all route through it
    so every recompute-from-params surface shows the same number.
    Non-finite / None samples are skipped.
    """
    cum = 0.0
    n = len(rates)
    for i in range(n):
        a = rates[i]
        if a is None or not math.isfinite(float(a)):
            continue
        a_f = float(a)
        nxt = rates[i + 1] if i + 1 < n else None
        b_f = (
            float(nxt)
            if nxt is not None and math.isfinite(float(nxt))
            else a_f
        )
        cum += (a_f + b_f) / 2.0 * _DAYS_PER_MONTH
    return cum


def compute_display_eur(
    *,
    qi: float,
    Di: float,
    b: float,
    Df: float,
    qo: float | None = None,
    peak_index_months: int | None = None,
    n_months: int = DISPLAY_EUR_N_MONTHS,
) -> float:
    """Display-convention EUR: trapezoid over the monthly ramp+Arps grid.

    The canonical *stored* EUR (``forecasts.eur``) is the closed-form /
    quad integral from ``compute_total_eur``; this discrete version
    differs from it only by quadrature error (<0.1% on Permian fits)
    and exists so every on-the-fly recompute (exports, well-stats,
    frontend probit dots) is identical to the others. The ramp prefix
    is included when ``qo`` / ``peak_index_months`` are supplied — the
    same evaluator that draws the chart curves builds the grid.
    """
    rates = build_ramp_arps_rate(
        n_months=n_months,
        qo=qo if qo is not None else qi,
        qi=qi,
        peak_index=int(peak_index_months or 0),
        Di=Di,
        b=b,
        Df=Df,
    )
    return trapezoid_eur(rates)


def display_eur_from_params(
    params: dict[str, Any] | None,
    *,
    n_months: int = DISPLAY_EUR_N_MONTHS,
) -> float | None:
    """``compute_display_eur`` from a ``forecasts.params``-shaped dict.

    Mirrors the frontend's ``eurFromForecastParams``: returns None when
    any core Arps param (qi/Di/b/Df) is missing or non-finite — the
    caller should fall back to the stored ``eur`` in that case. The
    optional ramp prefix (``qo`` / ``peak_index_months``) is honored
    when present and finite, dropped otherwise.
    """
    if not params:
        return None
    try:
        qi, Di, b, Df = (float(params[k]) for k in ("qi", "Di", "b", "Df"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (qi, Di, b, Df)):
        return None

    def _opt(key: str) -> float | None:
        v = params.get(key)
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    qo = _opt("qo")
    pim = _opt("peak_index_months")
    return compute_display_eur(
        qi=qi,
        Di=Di,
        b=b,
        Df=Df,
        qo=qo,
        peak_index_months=int(pim) if pim is not None else None,
        n_months=n_months,
    )


# ---------------------------------------------------------------------
# Preview helper (TC and per-well share this shape)
# ---------------------------------------------------------------------


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
) -> dict[str, Any]:
    """Build a ``fitted`` dict from an explicit parameter set.

    Used by the TC ``/preview`` endpoint and the save-with-override
    path; the shape mirrors what the TC P50 auto-fit emits so the
    frontend treats auto and manual identically. EUR is the raw
    horizon-bounded integral — no economic cutoff.
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


# ---------------------------------------------------------------------
# Internal — modified-hyperbolic rate via central-difference of the
# closed-form cum (matches the TC fit_p50 implementation).
# ---------------------------------------------------------------------


def _eval_modified_hyperbolic_rate(
    t_years: NDArray[np.float64],
    *,
    qi: float,
    Di: float,
    b: float,
    Df: float,
) -> NDArray[np.float64]:
    """Closed-form modified-hyperbolic rate at each time. Calls the
    canonical evaluator in ``app.forecasting.models`` so we don't carry
    a second implementation. The previous central-difference-of-cum
    approach (lifted from the old TC fit_p50) became visible as a
    sharp kink at the ramp/Arps seam because the np.maximum clamp on
    ``t - eps`` collapses the central difference to a one-sided
    difference near t=0, returning ~qi/2 instead of qi at small t."""
    result = modified_hyperbolic(t_years, qi, Di, b, Df)
    return np.maximum(np.asarray(result, dtype=float), 0.0)
