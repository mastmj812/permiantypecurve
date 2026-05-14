"""EUR — estimated ultimate recovery.

Integrates the rate model from t=0 to min(horizon, t_at_economic_limit).
For unconventional wells with b ≥ 1, EUR diverges without the economic-limit
cutoff, so the cutoff is not optional — it's the only reason the integral
converges.

Units convention: rates are per-day (BOPD/MCFD/BWPD); time `t` is in years.
The integral q(t) dt has units (rate × years); we multiply by `DAYS_PER_YEAR`
to recover BBL / MCF / BBL of cumulative.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq

from app.forecasting.models import (
    arps_exponential,
    arps_harmonic,
    arps_hyperbolic,
    duong,
    modified_hyperbolic,
    switchover_time,
)
from app.forecasting.types import (
    DEFAULT_ECONOMIC_LIMIT_BOPD,
    DEFAULT_FORECAST_HORIZON_YEARS,
)

DAYS_PER_YEAR: float = 365.0


def _rate_at_t(model_type: str, params: dict[str, float], t: float) -> float:
    """Single-point rate evaluation — used by root-finders and quad."""
    if model_type == "arps_exponential":
        return float(arps_exponential(t, params["qi"], params["Di"]))
    if model_type == "arps_hyperbolic":
        return float(arps_hyperbolic(t, params["qi"], params["Di"], params["b"]))
    if model_type == "arps_harmonic":
        return float(arps_harmonic(t, params["qi"], params["Di"]))
    if model_type == "modified_hyperbolic":
        return float(
            modified_hyperbolic(
                t, params["qi"], params["Di"], params["b"], params["Df"]
            )
        )
    if model_type == "duong":
        return float(duong(t, params["q1"], params["m"], params["a"]))
    raise ValueError(f"unknown model_type: {model_type}")


def _solve_t_at_rate(
    target_rate: float, model_type: str, params: dict[str, float]
) -> float:
    """Time (years) at which the model's rate drops to `target_rate`.

    Returns inf if the rate never reaches the target (e.g. target > qi).
    Uses closed-form solutions where they exist; falls back to a bracketed
    root find for Duong.
    """
    qi = params.get("qi", params.get("q1", 0.0))
    if target_rate <= 0 or target_rate >= qi:
        return float("inf")

    if model_type == "arps_exponential":
        Di = params["Di"]
        if Di <= 0:
            return float("inf")
        return math.log(qi / target_rate) / Di

    if model_type == "arps_hyperbolic":
        Di, b = params["Di"], params["b"]
        if Di <= 0:
            return float("inf")
        if abs(b) < 1e-6:
            return math.log(qi / target_rate) / Di
        return ((qi / target_rate) ** b - 1.0) / (b * Di)

    if model_type == "arps_harmonic":
        Di = params["Di"]
        if Di <= 0:
            return float("inf")
        return (qi / target_rate - 1.0) / Di

    if model_type == "modified_hyperbolic":
        Di, b, Df = params["Di"], params["b"], params["Df"]
        t_s = switchover_time(Di, Df, b)
        if not math.isfinite(t_s):
            return _solve_t_at_rate(
                target_rate, "arps_hyperbolic", {"qi": qi, "Di": Di, "b": b}
            )
        q_s = qi * (Df / Di) ** (1.0 / b)
        if target_rate < q_s:
            # Reached after switchover, in the exponential tail.
            return t_s + math.log(q_s / target_rate) / Df
        # Reached during the hyperbolic phase.
        return ((qi / target_rate) ** b - 1.0) / (b * Di)

    if model_type == "duong":
        try:
            return brentq(
                lambda t: _rate_at_t(model_type, params, t) - target_rate,
                1e-3,
                500.0,
                xtol=1e-4,
            )
        except ValueError:
            return float("inf")

    return float("inf")


def compute_eur(
    model_type: str,
    params: dict[str, float],
    *,
    horizon_years: float = DEFAULT_FORECAST_HORIZON_YEARS,
    economic_limit: float = DEFAULT_ECONOMIC_LIMIT_BOPD,
) -> float:
    """Cumulative production from t=0 to min(horizon, t_econ_limit).

    Returned units match the rate's per-day unit aggregated to total volume
    (BOPD → BBL, MCFD → MCF, BWPD → BBL).
    """
    t_econ = _solve_t_at_rate(economic_limit, model_type, params)
    t_end = min(horizon_years, t_econ)
    if t_end <= 0:
        return 0.0

    if model_type == "arps_exponential":
        qi, Di = params["qi"], params["Di"]
        if Di <= 0:
            return qi * t_end * DAYS_PER_YEAR  # constant-rate sanity fallback
        cum_per_unit_time = qi / Di * (1.0 - np.exp(-Di * t_end))
        return float(cum_per_unit_time * DAYS_PER_YEAR)

    integral, _err = quad(
        lambda t: _rate_at_t(model_type, params, t),
        0.0,
        t_end,
        limit=200,
    )
    return float(integral * DAYS_PER_YEAR)
