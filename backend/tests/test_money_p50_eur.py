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

import pytest

from app.forecasting.eur import compute_eur
from app.forecasting.ramp_arps import build_ramp_arps_rate
from app.type_curves.aggregate import WellSeries, aggregate
from app.type_curves.fit_p50 import fit_p50_series

# Pinned P50 oil EUR per 1,000 ft (bbl / 1000 ft) for the fixed cohort below.
# Re-pin ONLY on a deliberate math change, and call it out in the PR.
#
# History: 61,642.7 (2026-05 → 2026-09) was pinned while (a) b was frozen at
# 1.00 by the cum_hyperbolic harmonic hand-off and (b) this cohort was built
# from MONTH-AVERAGED rates, which is not what production feeds fit_p50 (see
# _decline_profile). Against the cohort's true 50-yr EUR it read +1.2%.
# 60,959.3 (2026-09-22): b free, qi cap on the peak-month average, point-
# sampled cohort, trapezoid cum target in fit_p50 — reads +0.10% vs truth
# (60,897.2); the residual is the P50 fit's own discretization.
PINNED_P50_OIL_EUR_PER_1000FT: float = 60_959.3
TOLERANCE: float = 0.005  # 0.5%
N_MONTHS: int = 48

# The cohort's truth, so the pin can be checked against something absolute.
_TRUE = {"qi": 800.0, "Di": 2.0, "b": 1.0, "Df": 0.08}  # nominal Di 2.0/yr ≈ 67% eff.


def _decline_profile(
    qi: float, di_nominal: float, b: float, df: float, n: int = N_MONTHS
) -> list[float]:
    """POINT-SAMPLED monthly rate series, rates[i] = q(t = i months), peak
    at month 0 — the same convention the type-curve loader feeds
    ``aggregate``/``fit_p50`` in production (``loader._forecast_rates`` →
    ``build_ramp_arps_rate``), whose peak value IS the instantaneous qi.
    Di is NOMINAL per-year.

    An earlier version built month-AVERAGED rates here; fit_p50 caps qi
    at the observed peak (qi_anchor_hi_basis="instantaneous"), so that
    convention forced qi ~7% under truth and the fitted EUR carried a
    -2.6% bias that had nothing to do with the model."""
    return build_ramp_arps_rate(n_months=n, qo=qi, qi=qi, peak_index=0, Di=di_nominal, b=b, Df=df)


def _fixed_cohort() -> list[WellSeries]:
    base = _decline_profile(qi=_TRUE["qi"], di_nominal=_TRUE["Di"], b=_TRUE["b"], df=_TRUE["Df"])
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


def test_pinned_value_is_the_cohorts_true_eur() -> None:
    """The pin is not just self-consistent: the median well IS the central
    profile (factor 1.0, 10,000 ft), so the exported P50 EUR/1000 ft must
    reproduce that well's closed-form 50-yr integral. Guards against
    re-pinning a number that merely encodes a fit bias."""
    true_per_1000ft = compute_eur("modified_hyperbolic", _TRUE, horizon_years=50.0) / 10.0
    assert pytest.approx(true_per_1000ft, rel=TOLERANCE) == PINNED_P50_OIL_EUR_PER_1000FT


def test_p50_percentile_orientation_is_spe() -> None:
    # SPE/PRMS: P10 is the HIGH case, P90 the LOW case — P10 >= P50 >= P90.
    agg = aggregate(_fixed_cohort(), normalization_basis="per_lateral_ft")
    eur = agg.streams["oil"].implied_eur_per_1000ft
    assert eur["p10"] >= eur["p50"] >= eur["p90"], (
        f"percentile orientation wrong: p10={eur['p10']:.0f}, p50={eur['p50']:.0f}, "
        f"p90={eur['p90']:.0f} (must be p10 >= p50 >= p90)"
    )
