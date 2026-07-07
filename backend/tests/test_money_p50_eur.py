"""End-to-end "money test" for the type-curve export.

A fixed synthetic cohort's EXPORTED P50 EUR per 1,000 ft of lateral must match
a pinned value within 0.5%. This is the tripwire for the next convention-level
bug: in one shot it exercises the cohort aggregation, the SPE P50 percentile
(P10 HIGH >= P50 >= P90 LOW), the ramp+Arps P50 fit, and the 50-yr technical
EUR integral (economic_limit=0).

The pinned number is ``fit_p50_series(agg.p50)["eur_per_unit"]`` — the value
app/exports/param_row.py writes to the CSV and the workspace display reads.
``implied_eur_per_1000ft`` is only a look-back QC artifact, not the published
EUR, so we do NOT pin that.

Pure functions only — no database, no Supabase.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.forecasting.cumulative import cum_modified_hyperbolic
from app.forecasting.eur import DAYS_PER_YEAR
from app.type_curves.aggregate import WellSeries, aggregate
from app.type_curves.fit_p50 import fit_p50_series

# Pinned P50 oil EUR per 1,000 ft (bbl / 1000 ft) for the fixed cohort below.
# Re-pin ONLY on a deliberate math change, and call it out in the PR.
PINNED_P50_OIL_EUR_PER_1000FT: float = 61_642.7
TOLERANCE: float = 0.005  # 0.5%
N_MONTHS: int = 48


def _decline_profile(
    qi: float, di_nominal: float, b: float, df: float, n: int = N_MONTHS
) -> list[float]:
    """Monthly calendar-day rate series for a modified-hyperbolic decline,
    peak at month 0 (a post-peak series, as ``aggregate`` expects). Di is
    NOMINAL per-year."""
    t = (np.arange(n) + 0.5) / 12.0
    eps = 1.0 / 24.0  # half-month, in years
    lo = cum_modified_hyperbolic(np.maximum(t - eps, 0.0), qi, di_nominal, b, df)
    hi = cum_modified_hyperbolic(t + eps, qi, di_nominal, b, df)
    return [float(x) for x in (hi - lo) / (2 * eps * DAYS_PER_YEAR)]


def _fixed_cohort() -> list[WellSeries]:
    # qi 800 BOPD, nominal Di 2.0/yr (~86% effective yr-1), b 1.0, Df 8%/yr.
    base = _decline_profile(qi=800.0, di_nominal=2.0, b=1.0, df=0.08)
    # Five wells spread around the central profile so the P50 is the median
    # well; all 10,000 ft lateral, so per-1,000-ft normalization is a clean /10.
    factors = [0.85, 0.925, 1.0, 1.075, 1.15]
    return [
        WellSeries(
            api10=f"tc{i}",
            lateral_ft=10_000.0,
            proppant_lbs=None,
            oil_rates=[r * f for r in base],
            gas_rates=[None] * N_MONTHS,
            water_rates=[None] * N_MONTHS,
        )
        for i, f in enumerate(factors)
    ]


def test_exported_p50_oil_eur_per_1000ft_is_pinned() -> None:
    agg = aggregate(_fixed_cohort(), normalization_basis="per_lateral_ft")
    fitted = fit_p50_series(agg.streams["oil"].p50)
    assert fitted is not None, "P50 fit failed for the fixed cohort"

    exported = fitted["eur_per_unit"]
    assert exported == pytest.approx(PINNED_P50_OIL_EUR_PER_1000FT, rel=TOLERANCE), (
        f"P50 oil EUR/1000ft = {exported:.1f}, pinned "
        f"{PINNED_P50_OIL_EUR_PER_1000FT:.1f} (±{TOLERANCE * 100:.1f}%). The "
        f"type-curve EUR the tool exports moved — investigate a convention-level "
        f"change before re-pinning."
    )


def test_p50_percentile_orientation_is_spe() -> None:
    # SPE/PRMS: P10 is the HIGH case, P90 the LOW case — P10 >= P50 >= P90.
    agg = aggregate(_fixed_cohort(), normalization_basis="per_lateral_ft")
    eur = agg.streams["oil"].implied_eur_per_1000ft
    assert eur["p10"] >= eur["p50"] >= eur["p90"], (
        f"percentile orientation wrong: p10={eur['p10']:.0f}, p50={eur['p50']:.0f}, "
        f"p90={eur['p90']:.0f} (must be p10 >= p50 >= p90)"
    )
