"""Decline-metric conversions.

The Arps equation uses *nominal* Di — what appears directly in the rate
formula. Petroleum engineers usually quote *effective* decline — the
percent rate drop over one year. They differ substantially for steep
unconventional wells.

Cheat sheet for b=1.0:

    Effective Di | Nominal Di
    -----------  | -----------
        30%      |    0.43
        50%      |    1.00
        65%      |    1.86
        70%      |    2.33      ← Permian-typical
        80%      |    4.00

Conversions:
    hyperbolic: D_eff = 1 - 1 / (1 + b * Di)^(1/b)
    exponential: D_eff = 1 - exp(-Di)
    harmonic (b=1): D_eff = Di / (1 + Di)
"""

from __future__ import annotations

import math


def effective_decline_first_year(Di_nominal: float, b: float | None = None) -> float:
    """Convert nominal Di (per year) to effective annual decline (fraction).

    Returns a value in [0, 1) — the fraction of rate lost in year 1.
    For b near 0, uses the exponential form. For b near 1, uses harmonic
    (avoids the (1-1/b) singularity in the general formula).
    """
    if Di_nominal <= 0:
        return 0.0
    if b is None or abs(b) < 1e-6:
        return 1.0 - math.exp(-Di_nominal)
    if abs(b - 1.0) < 1e-4:
        return Di_nominal / (1.0 + Di_nominal)
    return 1.0 - 1.0 / (1.0 + b * Di_nominal) ** (1.0 / b)


def nominal_from_effective(D_eff: float, b: float | None = None) -> float:
    """Inverse — given an effective annual decline, recover nominal Di.

    Convenient for UI input forms where the user types "70%" and we want
    to feed nominal Di to the model.
    """
    if D_eff <= 0:
        return 0.0
    if D_eff >= 1.0:
        return float("inf")
    if b is None or abs(b) < 1e-6:
        return -math.log(1.0 - D_eff)
    if abs(b - 1.0) < 1e-4:
        return D_eff / (1.0 - D_eff)
    # From (1 + b*Di)^(1/b) = 1 / (1 - D_eff)  →
    # 1 + b*Di = (1 - D_eff)^(-b)  →
    # Di = ((1 - D_eff)^(-b) - 1) / b
    return ((1.0 - D_eff) ** (-b) - 1.0) / b
