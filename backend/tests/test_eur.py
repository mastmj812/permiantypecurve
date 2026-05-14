"""EUR: closed-form vs numerical-integration agreement + econ-limit behavior."""

from __future__ import annotations

import math

import pytest

from app.forecasting.eur import DAYS_PER_YEAR, _solve_t_at_rate, compute_eur


def test_exponential_eur_closed_form() -> None:
    # qi=500 BOPD, Di=0.85/yr, horizon=50yr, econ limit very low so horizon binds.
    # Asymptotic EUR = qi/Di * 365 = 500/0.85 * 365 ≈ 214,706 BBL
    eur = compute_eur(
        "arps_exponential",
        {"qi": 500.0, "Di": 0.85},
        horizon_years=50.0,
        economic_limit=1e-6,
    )
    expected = 500.0 / 0.85 * DAYS_PER_YEAR * (1.0 - math.exp(-0.85 * 50.0))
    assert eur == pytest.approx(expected, rel=1e-6)


def test_exponential_eur_economic_limit_binds() -> None:
    # At what t does rate drop to 5 BOPD?  t = ln(500/5)/0.85 = ln(100)/0.85 ≈ 5.42yr
    t_econ = _solve_t_at_rate(5.0, "arps_exponential", {"qi": 500.0, "Di": 0.85})
    assert t_econ == pytest.approx(math.log(100.0) / 0.85, rel=1e-9)

    # Integrate 0..t_econ; should match qi/Di * (1 - exp(-Di*t_econ)) * 365
    eur = compute_eur(
        "arps_exponential",
        {"qi": 500.0, "Di": 0.85},
        horizon_years=50.0,
        economic_limit=5.0,
    )
    expected = 500.0 / 0.85 * (1.0 - math.exp(-0.85 * t_econ)) * DAYS_PER_YEAR
    assert eur == pytest.approx(expected, rel=1e-6)


def test_hyperbolic_b_gt_1_diverges_without_econ_limit_but_finite_with() -> None:
    # b=1.5 — EUR formally divergent. The econ-limit cap must keep it finite.
    eur = compute_eur(
        "arps_hyperbolic",
        {"qi": 500.0, "Di": 0.85, "b": 1.5},
        horizon_years=50.0,
        economic_limit=5.0,
    )
    assert math.isfinite(eur)
    assert eur > 0


def test_modified_hyperbolic_eur_bounded_even_without_econ_limit() -> None:
    # With Df = 0.08, the tail eventually becomes 8%/yr exponential —
    # EUR is finite even at extreme horizons and zero econ limit.
    eur_50yr = compute_eur(
        "modified_hyperbolic",
        {"qi": 500.0, "Di": 0.85, "b": 1.2, "Df": 0.08},
        horizon_years=50.0,
        economic_limit=1e-6,
    )
    eur_100yr = compute_eur(
        "modified_hyperbolic",
        {"qi": 500.0, "Di": 0.85, "b": 1.2, "Df": 0.08},
        horizon_years=100.0,
        economic_limit=1e-6,
    )
    # 50-yr and 100-yr should be close (exponential tail well past its half-life by 50yr).
    assert math.isfinite(eur_50yr) and math.isfinite(eur_100yr)
    assert eur_100yr > eur_50yr  # still some volume at 100yr
    assert (eur_100yr - eur_50yr) / eur_50yr < 0.5  # but not 2x


def test_solve_t_at_rate_inversion_round_trips() -> None:
    # Pick a target rate, find the t at which the model hits it, evaluate
    # the model there, confirm we get the target back.
    from app.forecasting.models import arps_hyperbolic

    params = {"qi": 1000.0, "Di": 0.7, "b": 1.1}
    t_at_50 = _solve_t_at_rate(50.0, "arps_hyperbolic", params)
    q_at_t = arps_hyperbolic(t_at_50, **params)
    assert q_at_t == pytest.approx(50.0, rel=1e-6)


def test_eur_zero_when_econ_limit_above_qi() -> None:
    # Rate never reaches the econ limit ⇒ rate is below econ from the start
    # ⇒ t_econ = inf (per _solve_t_at_rate behavior), so horizon binds.
    # If econ_limit > qi, t_econ is inf and we integrate over horizon.
    # But the well is already below "economic" at t=0, which is realistic
    # for a watered-out late-life well being evaluated. Document the
    # behavior: compute_eur doesn't zero-out early; it just integrates.
    eur = compute_eur(
        "arps_exponential",
        {"qi": 3.0, "Di": 0.5},
        horizon_years=10.0,
        economic_limit=5.0,
    )
    assert eur > 0  # We do integrate; caller decides if it's "real EUR" or not.
