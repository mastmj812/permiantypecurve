"""Closed-form cumulative-production functions per model.

These are used as the fit target for `fit_rate_cum`. Keeping them in
closed form (rather than scipy.integrate.quad inside the curve_fit inner
loop) keeps fitting fast and well-behaved.

Cumulative is returned in volume units (BBL / MCF / BBL of water), i.e.
the rate's per-day units integrated and multiplied by DAYS_PER_YEAR=365.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.models import switchover_time

_B_EXP_THRESHOLD: float = 1e-6
_B_HARMONIC_THRESHOLD: float = 1e-4  # |b - 1|


def cum_exponential(
    t: NDArray[np.float64], qi: float, Di: float
) -> NDArray[np.float64]:
    """Q(t) = qi / Di * (1 - exp(-Di*t)) * DAYS_PER_YEAR"""
    t_arr = np.asarray(t, dtype=float)
    if Di <= 0:
        return qi * t_arr * DAYS_PER_YEAR
    return qi / Di * (1.0 - np.exp(-Di * t_arr)) * DAYS_PER_YEAR


def cum_harmonic(
    t: NDArray[np.float64], qi: float, Di: float
) -> NDArray[np.float64]:
    """Q(t) = qi / Di * ln(1 + Di*t) * DAYS_PER_YEAR  (b=1 case)"""
    t_arr = np.asarray(t, dtype=float)
    if Di <= 0:
        return qi * t_arr * DAYS_PER_YEAR
    return qi / Di * np.log1p(Di * t_arr) * DAYS_PER_YEAR


def cum_hyperbolic(
    t: NDArray[np.float64], qi: float, Di: float, b: float
) -> NDArray[np.float64]:
    """Q(t) = qi / (Di*(1-b)) * (1 - (1+b*Di*t)**(1-1/b)) * DAYS_PER_YEAR

    Diverges as t→∞ for b ≥ 1; finite for any finite t. The fitter calls
    this only over the historical data range, so divergence isn't an issue
    during the fit — EUR uses the economic-limit cutoff to stay bounded.
    """
    t_arr = np.asarray(t, dtype=float)
    if abs(b) < _B_EXP_THRESHOLD:
        return cum_exponential(t_arr, qi, Di)
    if abs(b - 1.0) < _B_HARMONIC_THRESHOLD:
        return cum_harmonic(t_arr, qi, Di)
    factor = qi / (Di * (1.0 - b))
    return factor * (1.0 - np.power(1.0 + b * Di * t_arr, 1.0 - 1.0 / b)) * DAYS_PER_YEAR


def cum_modified_hyperbolic(
    t: NDArray[np.float64], qi: float, Di: float, b: float, Df: float
) -> NDArray[np.float64]:
    """Piecewise cumulative for modified hyperbolic.

    Hyperbolic phase up to t_switch, exponential at Df thereafter.
    Continuous at t_switch by construction; the additive Q at switchover
    keeps the two pieces glued together.
    """
    t_arr = np.asarray(t, dtype=float)
    t_s = switchover_time(Di, Df, b)

    if not np.isfinite(t_s):
        # No switchover (Df≥Di or b≈0) — pure hyperbolic/exponential.
        return cum_hyperbolic(t_arr, qi, Di, b)

    Q_s = cum_hyperbolic(np.array([t_s]), qi, Di, b)[0]
    q_s = qi * (Df / Di) ** (1.0 / b)  # rate at switchover
    # After switchover: Q(t) = Q_s + q_s/Df * (1 - exp(-Df*(t-t_s))) * 365
    tail_factor = q_s / Df if Df > 0 else 0.0
    tail = Q_s + tail_factor * (1.0 - np.exp(-Df * (t_arr - t_s))) * DAYS_PER_YEAR
    hyperbolic = cum_hyperbolic(t_arr, qi, Di, b)
    return np.where(t_arr <= t_s, hyperbolic, tail)
