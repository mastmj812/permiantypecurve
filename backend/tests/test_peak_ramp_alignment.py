"""peak_ramp alignment: peak-aligned aggregation with a ramp lookback.

The motivating defect: under first_prod_month alignment, wells peak at
different months (ramp lengths differ), so the cross-well percentile at
any month mixes declining wells with still-ramping wells. The panel's
peak is below the median of the individual peaks (qi suppressed) and
the early decline is propped up by late peakers (Di flattened). The
peak_ramp loader slides each well so its peak lands on the cohort
anchor M, keeping its real ramp in the months before — these tests pin
that the shift mechanism places peaks correctly and that the recovered
qi/Di on a synthetic cohort match the true well parameters where the
first-prod panel demonstrably understates them.
"""

from __future__ import annotations

import pytest

from app.type_curves.aggregate import WellSeries, aggregate
from app.type_curves.fit_p50 import fit_p50_series
from app.type_curves.loader import _forecast_rates

# One "true" well, repeated with different ramp lengths: 1,000 BOPD
# peak, nominal Di 2.0/yr at b=1, Df 0.08, ramping from 300 BOPD.
_TRUE_QI = 1000.0
_TRUE_DI = 2.0


def _params(ramp_months: int) -> dict[str, float | int]:
    return {
        "qi": _TRUE_QI,
        "Di": _TRUE_DI,
        "b": 1.0,
        "Df": 0.08,
        "qo": 300.0,
        "peak_index_months": ramp_months,
    }


def _panel(ramps: list[int], *, anchor: int | None, n_months: int = 120):
    """Build a WellSeries panel; anchor=None → first-prod (no shift)."""
    wells = []
    for i, r in enumerate(ramps):
        shift = (anchor - r) if anchor is not None else 0
        rates = _forecast_rates(_params(r), n_months, include_ramp=True, shift_months=shift)
        wells.append(
            WellSeries(
                api10=f"well{i}",
                lateral_ft=1000.0,
                proppant_lbs=None,
                oil_rates=rates,
                gas_rates=[None] * n_months,
                water_rates=[None] * n_months,
            )
        )
    return aggregate(wells, n_months=n_months)


def test_shift_places_every_peak_on_the_anchor() -> None:
    M = 2
    for ramp in (0, 1, 2, 3, 4):
        rates = _forecast_rates(_params(ramp), 24, include_ramp=True, shift_months=M - ramp)
        assert len(rates) == 24
        # Peak (the max finite value) must land exactly on month M.
        finite = [(i, v) for i, v in enumerate(rates) if v is not None]
        peak_i, peak_v = max(finite, key=lambda iv: iv[1])
        assert peak_i == M
        assert peak_v == pytest.approx(_TRUE_QI)
        # Shorter-than-anchor ramps front-pad with nulls (no signal
        # before the well's onset); longer ones lose their earliest
        # ramp months — never fabricate data.
        lead_nulls = M - ramp if ramp < M else 0
        assert rates[:lead_nulls] == [None] * lead_nulls


def test_peak_ramp_recovers_qi_and_di_where_first_prod_smears() -> None:
    # Five IDENTICAL wells differing only in ramp length. Any honest
    # aggregation should report the well parameters back.
    ramps = [0, 1, 2, 3, 4]
    smeared = _panel(ramps, anchor=None).streams["oil"].p50
    aligned = _panel(ramps, anchor=2).streams["oil"].p50

    # qi: the aligned panel's peak is the true peak (every well at max
    # in the same month); the staggered panel never sees them together
    # and underrates the peak by >10% on this cohort.
    aligned_peak = max(v for v in aligned if v is not None)
    smeared_peak = max(v for v in smeared if v is not None)
    assert aligned_peak == pytest.approx(_TRUE_QI, rel=1e-6)
    assert smeared_peak < 0.9 * _TRUE_QI

    # Di: fit both panels with the production fitter. The aligned fit
    # recovers the true nominal Di; the smeared fit comes out shallower.
    fit_aligned = fit_p50_series(aligned)
    fit_smeared = fit_p50_series(smeared)
    assert fit_aligned is not None and fit_smeared is not None
    assert fit_aligned["Di"] == pytest.approx(_TRUE_DI, rel=0.05)
    assert fit_smeared["Di"] < fit_aligned["Di"]

    # And the aligned fit still carries a REAL ramp (the whole reason
    # peak_month alignment wasn't acceptable): peak at the anchor month
    # with a positive ramp contribution to EUR.
    assert fit_aligned["peak_index"] == 2
    assert fit_aligned["ramp_eur"] > 0
