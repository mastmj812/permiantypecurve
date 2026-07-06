"""The shared display-EUR convention (ramp_arps.trapezoid_eur and
friends).

Every recompute-from-params surface — deal xlsx/pptx well rows
(``exports.well_rows.eur_monthly_trapezoid``), the TC well-stats
endpoint, the per-percentile export EURs, and the frontend's
``eurFromForecastParams`` (TS mirror) — routes through one helper so
they all show the same number. These tests pin:

  * the trapezoid rule itself (constant-rate closed form, None/NaN
    handling, flat extrapolation of the final month);
  * agreement with the canonical closed-form ``compute_total_eur``
    (quadrature error only — well under 1% on Permian-shaped fits);
  * ramp-prefix inclusion (the old export path dropped it);
  * the params-dict wrapper's missing/non-finite fallbacks.
"""

from __future__ import annotations

import math

import pytest

from app.exports.well_rows import eur_monthly_trapezoid
from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.ramp_arps import (
    compute_display_eur,
    compute_ramp_eur,
    compute_total_eur,
    display_eur_from_params,
    trapezoid_eur,
)

_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0

# Permian-typical modified-hyperbolic fit.
_PARAMS = {"qi": 850.0, "Di": 2.1, "b": 1.05, "Df": 0.08}
_RAMP = {"qo": 240.0, "peak_index_months": 3}


def test_trapezoid_eur_constant_rate_closed_form() -> None:
    # Constant rate: trapezoid == rectangle == rate × months × dt.
    rates = [100.0] * 600
    assert trapezoid_eur(rates) == pytest.approx(100.0 * 600 * _DAYS_PER_MONTH)


def test_trapezoid_eur_skips_non_finite_and_none() -> None:
    rates = [100.0, None, float("nan"), 100.0]
    # None/NaN months contribute nothing; the finite months still
    # integrate (each flat-extrapolated against its own value here).
    assert trapezoid_eur(rates) == pytest.approx(2 * 100.0 * _DAYS_PER_MONTH)


def test_trapezoid_eur_linear_decline_exact() -> None:
    # Trapezoid is exact for a linear rate profile: 3 samples 100→50→0
    # over 2 month-intervals + the final flat-extrapolated 0-month.
    rates = [100.0, 50.0, 0.0]
    assert trapezoid_eur(rates) == pytest.approx(
        (75.0 + 25.0 + 0.0) * _DAYS_PER_MONTH
    )


def test_display_eur_matches_closed_form_without_ramp() -> None:
    display = compute_display_eur(**_PARAMS)
    closed = compute_total_eur(model_type="modified_hyperbolic", params=_PARAMS)
    assert display == pytest.approx(closed, rel=0.01)


def test_display_eur_matches_closed_form_with_ramp() -> None:
    params = {**_PARAMS, **_RAMP}
    display = compute_display_eur(
        **_PARAMS,
        qo=_RAMP["qo"],
        peak_index_months=_RAMP["peak_index_months"],
    )
    closed = compute_total_eur(model_type="modified_hyperbolic", params=params)
    assert display == pytest.approx(closed, rel=0.01)


def test_display_eur_includes_ramp_volume() -> None:
    # The ramp-aware EUR replaces the first peak_index months of Arps
    # decline with the (lower) qo→qi ramp; the difference from the
    # no-ramp evaluation must be material and bounded by the two
    # segments' areas. Guards against regressing to the old qo=qi /
    # peak_index=0 shortcut that dropped the ramp entirely.
    with_ramp = compute_display_eur(
        **_PARAMS, qo=_RAMP["qo"], peak_index_months=_RAMP["peak_index_months"]
    )
    without_ramp = compute_display_eur(**_PARAMS)
    ramp_area = compute_ramp_eur(
        qo=_RAMP["qo"], qi=_PARAMS["qi"], peak_index=_RAMP["peak_index_months"]
    )
    assert with_ramp != pytest.approx(without_ramp, rel=1e-3)
    # Sanity bound: the swap can't change the total by more than the
    # larger of the two early-month segments.
    assert abs(with_ramp - without_ramp) < max(
        ramp_area,
        _PARAMS["qi"] * _RAMP["peak_index_months"] * _DAYS_PER_MONTH,
    )


def test_display_eur_from_params_requires_core_params() -> None:
    assert display_eur_from_params(None) is None
    assert display_eur_from_params({}) is None
    assert display_eur_from_params({"qi": 850.0, "Di": 2.1, "b": 1.05}) is None
    bad = dict(_PARAMS)
    bad["Di"] = float("nan")
    assert display_eur_from_params(bad) is None


def test_display_eur_from_params_ignores_broken_ramp_fields() -> None:
    # Non-finite / non-numeric ramp fields degrade to pure Arps instead
    # of erroring (mirrors the frontend's eurFromForecastParams).
    params = {**_PARAMS, "qo": float("nan"), "peak_index_months": None}
    assert display_eur_from_params(params) == pytest.approx(
        compute_display_eur(**_PARAMS)
    )


def test_export_well_rows_use_shared_convention() -> None:
    # The deal-export helper is a thin wrapper over the shared helper —
    # ramp included, trapezoid rule, same number every other surface
    # shows. (The old inline version was a rectangle sum with the ramp
    # dropped.)
    params = {**_PARAMS, **_RAMP}
    assert eur_monthly_trapezoid(params) == pytest.approx(
        display_eur_from_params(params)
    )
    assert eur_monthly_trapezoid(params) == pytest.approx(
        compute_total_eur(model_type="modified_hyperbolic", params=params),
        rel=0.01,
    )
    assert eur_monthly_trapezoid({"qi": 100.0}) is None


def test_display_eur_finite_for_b_above_one() -> None:
    # b ≥ 1 hyperbolic diverges only at infinite horizon; the 600-month
    # grid must stay finite and positive.
    val = compute_display_eur(qi=900.0, Di=3.5, b=1.2, Df=0.06)
    assert math.isfinite(val) and val > 0
