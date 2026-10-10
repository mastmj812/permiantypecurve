"""Decline-curve models.

All rate functions take time `t` in **years** and return rate in the same
units as `qi` (e.g. BOPD if qi is BOPD). Vectorized over numpy arrays of t.

Conventions:
  * `Di` is the nominal initial decline (per year). For hyperbolic this is
    the Arps "nominal" rate; effective decline at t=0 differs slightly.
  * `b` is the hyperbolic exponent, typically 0..2 for unconventional plays.
  * `Df` is the terminal exponential decline (per year) for modified
    hyperbolic — usually 0.08 (8%/yr) in Permian practice.
"""

from __future__ import annotations

from typing import overload

import numpy as np
from numpy.typing import NDArray

# Below this exponent the hyperbolic form is numerically indistinguishable
# from exponential; we route through the exponential branch to dodge a
# pow(small_positive, large) overflow at long horizons.
_B_EXPONENTIAL_THRESHOLD: float = 1e-6


@overload
def arps_exponential(t: float, qi: float, Di: float) -> float: ...
@overload
def arps_exponential(t: NDArray[np.float64], qi: float, Di: float) -> NDArray[np.float64]: ...


def arps_exponential(t, qi, Di):  # type: ignore[no-untyped-def]
    """q(t) = qi * exp(-Di * t)"""
    return qi * np.exp(-Di * np.asarray(t, dtype=float))


@overload
def arps_hyperbolic(t: float, qi: float, Di: float, b: float) -> float: ...
@overload
def arps_hyperbolic(
    t: NDArray[np.float64], qi: float, Di: float, b: float
) -> NDArray[np.float64]: ...


def arps_hyperbolic(t, qi, Di, b):  # type: ignore[no-untyped-def]
    """q(t) = qi / (1 + b*Di*t)**(1/b)

    Degenerates to exponential as b → 0; we hand off to `arps_exponential`
    below the numeric threshold to avoid (1+ε)**(1/ε) blowups.
    """
    if abs(b) < _B_EXPONENTIAL_THRESHOLD:
        return arps_exponential(t, qi, Di)
    t_arr = np.asarray(t, dtype=float)
    return qi / np.power(1.0 + b * Di * t_arr, 1.0 / b)


@overload
def arps_harmonic(t: float, qi: float, Di: float) -> float: ...
@overload
def arps_harmonic(t: NDArray[np.float64], qi: float, Di: float) -> NDArray[np.float64]: ...


def arps_harmonic(t, qi, Di):  # type: ignore[no-untyped-def]
    """q(t) = qi / (1 + Di*t)  — equivalent to Arps hyperbolic with b=1."""
    return qi / (1.0 + Di * np.asarray(t, dtype=float))


def switchover_time(Di: float, Df: float, b: float) -> float:
    """Return t where hyperbolic instantaneous decline drops to Df.

    Solve  D(t) = Di / (1 + b*Di*t) = Df  →  t = (Di/Df - 1) / (b*Di).
    Returns infinity when there's no switchover (b≈0 or Df ≥ Di), letting
    callers treat the curve as pure hyperbolic / exponential for the full
    horizon.
    """
    if Df <= 0 or b <= _B_EXPONENTIAL_THRESHOLD or Df >= Di:
        return float("inf")
    return (Di / Df - 1.0) / (b * Di)


def modified_hyperbolic(
    t: float | NDArray[np.float64], qi: float, Di: float, b: float, Df: float
) -> float | NDArray[np.float64]:
    """Hyperbolic until instantaneous decline D(t) reaches Df, then
    exponential at Df from the switchover point. Continuous by construction.

    Switchover algebra:
      t_s = (Di/Df - 1) / (b*Di)
      q_s = qi * (Df/Di)**(1/b)                  -- pure-hyperbolic value at t_s
      q(t > t_s) = q_s * exp(-Df * (t - t_s))
    """
    t_s = switchover_time(Di, Df, b)
    t_arr = np.asarray(t, dtype=float)

    # Pure hyperbolic / exponential cases — no switchover.
    if not np.isfinite(t_s):
        if abs(b) < _B_EXPONENTIAL_THRESHOLD:
            out = arps_exponential(t_arr, qi, Di)
        else:
            out = arps_hyperbolic(t_arr, qi, Di, b)
        return out.item() if np.isscalar(t) else out

    q_s = qi * (Df / Di) ** (1.0 / b)
    before = arps_hyperbolic(t_arr, qi, Di, b)
    after = q_s * np.exp(-Df * (t_arr - t_s))
    out = np.where(t_arr <= t_s, before, after)
    return float(out) if np.isscalar(t) else out


def duong(
    t: float | NDArray[np.float64], q1: float, m: float, a: float
) -> float | NDArray[np.float64]:
    """Duong's method for shale wells.

    q(t) = q1 * t**(-m) * exp((a/(1-m)) * (t**(1-m) - 1))

    `q1` is the rate at t=1 year. `m` (>1 typically) and `a` are shape
    parameters that come out of the linear-regression form of the model.
    """
    t_arr = np.maximum(np.asarray(t, dtype=float), 1e-9)  # avoid t**negative at 0
    if abs(1.0 - m) < _B_EXPONENTIAL_THRESHOLD:
        # m → 1 limit: q1 * t**(-1) * exp(a * ln(t)) = q1 * t**(a-1)
        out = q1 * np.power(t_arr, a - 1.0)
    else:
        exponent_factor = a / (1.0 - m) * (np.power(t_arr, 1.0 - m) - 1.0)
        out = q1 * np.power(t_arr, -m) * np.exp(exponent_factor)
    return float(out) if np.isscalar(t) else out
