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
# |z| below which phi(z) = -expm1(-z)/z is evaluated by its series. Only
# guards the literal 0/0 at b == 1; expm1 is accurate everywhere else.
_PHI_SERIES_THRESHOLD: float = 1e-12


def cum_exponential(t: NDArray[np.float64], qi: float, Di: float) -> NDArray[np.float64]:
    """Q(t) = qi / Di * (1 - exp(-Di*t)) * DAYS_PER_YEAR"""
    t_arr = np.asarray(t, dtype=float)
    if Di <= 0:
        return qi * t_arr * DAYS_PER_YEAR
    return qi / Di * (1.0 - np.exp(-Di * t_arr)) * DAYS_PER_YEAR


def cum_harmonic(t: NDArray[np.float64], qi: float, Di: float) -> NDArray[np.float64]:
    """Q(t) = qi / Di * ln(1 + Di*t) * DAYS_PER_YEAR  (b=1 case)"""
    t_arr = np.asarray(t, dtype=float)
    if Di <= 0:
        return qi * t_arr * DAYS_PER_YEAR
    return qi / Di * np.log1p(Di * t_arr) * DAYS_PER_YEAR


def cum_hyperbolic(t: NDArray[np.float64], qi: float, Di: float, b: float) -> NDArray[np.float64]:
    """Q(t) = qi / (Di*(1-b)) * (1 - (1+b*Di*t)**(1-1/b)) * DAYS_PER_YEAR

    Evaluated in the algebraically identical form

        Q = qi / (Di*b) * u * phi(z) * DAYS_PER_YEAR
        u = ln(1 + b*Di*t),  z = (1-b)/b * u,  phi(z) = -expm1(-z) / z

    which is exact AND smooth in b through b = 1 (phi(0) = 1 gives the
    harmonic qi/Di * ln(1+Di*t)). The textbook form cancels
    catastrophically as b→1, and the old remedy — hand off to
    ``cum_harmonic`` inside |b-1| < 1e-4 — made dQ/db exactly 0 there.
    Every fit starts at B_P0 = 1.0 and curve_fit's finite-difference step
    (~1.5e-8) never left that band, so b was frozen at 1.00 on ~90% of
    forecasts and on every type-curve P50 fit. Do not reintroduce a b≈1
    branch here.

    Diverges as t→∞ for b ≥ 1; finite for any finite t. The fitter calls
    this only over the historical data range, so divergence isn't an issue
    during the fit — EUR integrates the rate form over a fixed horizon.
    """
    t_arr = np.asarray(t, dtype=float)
    if abs(b) < _B_EXP_THRESHOLD:
        return cum_exponential(t_arr, qi, Di)
    if Di <= 0:
        return qi * t_arr * DAYS_PER_YEAR
    u = np.log1p(b * Di * t_arr)
    z = ((1.0 - b) / b) * u
    small = np.abs(z) < _PHI_SERIES_THRESHOLD
    z_safe = np.where(small, 1.0, z)
    phi = np.where(small, 1.0 - z / 2.0, -np.expm1(-z_safe) / z_safe)
    return qi / (Di * b) * u * phi * DAYS_PER_YEAR


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
