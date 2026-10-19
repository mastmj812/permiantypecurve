"""Ratio-mode stream fitted on a SEPARATE cohort (app.type_curves.ratio_cohort; engineering_db BOX D107). DB-free.

Pins: a ratio cohort equal to the curve's cohort reproduces build_ratio_fitted exactly; the ratio is learned from the
cohort but the forecast rides the CURVE's oil; the cohort survives in the saved series; validation refuses a cohort on a
non-ratio stream.
"""

from __future__ import annotations

import math

import pytest

from app.forecasting.ramp_arps import build_ramp_arps_rate, trapezoid_eur
from app.type_curves.ratio_cohort import (
    build_ratio_fitted_on_cohort,
    ratio_cohorts_from_series,
    validate_ratio_cohorts,
)
from app.type_curves.ratio_mode import build_ratio_fitted


def _oil(qi: float, Di: float = 2.0) -> tuple[dict, list[float]]:
    p = {"qi": qi, "Di": Di, "b": 1.0, "Df": 0.08, "qo": qi / 2.5, "peak_index": 2}
    grid = build_ramp_arps_rate(
        n_months=600,
        qo=p["qo"],
        qi=p["qi"],
        peak_index=p["peak_index"],
        Di=p["Di"],
        b=p["b"],
        Df=p["Df"],
    )
    return {**p, "eur_per_unit": trapezoid_eur(grid)}, grid


def test_same_cohort_reproduces_build_ratio_fitted():
    oil_fitted, grid = _oil(60.0)
    mean_oil = list(grid[:60])
    mean_water = [v * (0.8 + 0.01 * i) for i, v in enumerate(mean_oil)]  # rising WOR
    a = build_ratio_fitted(
        mean_oil=mean_oil, mean_stream=mean_water, oil_fitted=oil_fitted, n_months=600
    )
    b = build_ratio_fitted_on_cohort(
        cohort_mean_oil=mean_oil,
        cohort_mean_stream=mean_water,
        cohort_oil_fitted=oil_fitted,
        tc_oil_fitted=oil_fitted,
        n_months=600,
        n_wells=12,
        n_requested=12,
    )
    assert a is not None and b is not None
    for k in ("alpha", "beta", "sub_mode", "r2", "eur_per_unit"):
        assert b[k] == pytest.approx(a[k], rel=1e-12) if isinstance(a[k], float) else b[k] == a[k]
    assert b["smoothed_rate"] == pytest.approx(a["smoothed_rate"], rel=1e-12)
    assert b["ratio_cohort"] == {"n_wells": 12, "n_requested": 12}


def test_ratio_from_the_cohort_forecast_on_the_curve_oil():
    cohort_fit, cohort_grid = _oil(40.0)  # the measured-water cohort: different oil
    tc_fit, _ = _oil(80.0)  # the curve's own oil
    mean_oil = list(cohort_grid[:60])
    mean_water = [3.0 * v for v in mean_oil]  # constant WOR 3
    out = build_ratio_fitted_on_cohort(
        cohort_mean_oil=mean_oil,
        cohort_mean_stream=mean_water,
        cohort_oil_fitted=cohort_fit,
        tc_oil_fitted=tc_fit,
        n_months=600,
        n_wells=9,
        n_requested=11,
    )
    assert out is not None
    assert math.exp(out["alpha"]) == pytest.approx(3.0, rel=1e-6)
    # water = WOR 3 x the CURVE's oil (not the cohort's)
    assert out["eur_per_unit"] == pytest.approx(3.0 * tc_fit["eur_per_unit"], rel=1e-3)
    assert out["oil_ref"]["qi"] == 80.0
    assert out["ratio_cohort"] == {"n_wells": 9, "n_requested": 11}


def test_unusable_cohort_returns_none():
    tc_fit, _ = _oil(60.0)
    assert (
        build_ratio_fitted_on_cohort(
            cohort_mean_oil=[0.0] * 24,
            cohort_mean_stream=[0.0] * 24,
            cohort_oil_fitted=tc_fit,
            tc_oil_fitted=tc_fit,
            n_months=600,
            n_wells=3,
            n_requested=3,
        )
        is None
    )


def test_validate_and_read_back():
    assert validate_ratio_cohorts(None, {}) == {}
    assert validate_ratio_cohorts({"water": ["1", "2", "1"]}, {"water": "ratio"}) == {
        "water": ["1", "2"]
    }
    with pytest.raises(ValueError, match="needs stream_modes"):
        validate_ratio_cohorts({"water": ["1"]}, {})
    with pytest.raises(ValueError, match="empty"):
        validate_ratio_cohorts({"water": []}, {"water": "ratio"})
    with pytest.raises(ValueError, match="max"):
        validate_ratio_cohorts({"water": [str(i) for i in range(501)]}, {"water": "ratio"})
    assert ratio_cohorts_from_series(
        {"ratio_cohorts": {"water": {"api10s": ["4230100001"], "n_wells": 1}}}
    ) == {"water": ["4230100001"]}
    assert ratio_cohorts_from_series({}) == {} and ratio_cohorts_from_series(None) == {}
