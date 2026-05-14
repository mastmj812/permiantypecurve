"""Nominal ↔ effective Di round-trips."""

from __future__ import annotations

import math

import pytest

from app.forecasting.metrics import (
    effective_decline_first_year,
    nominal_from_effective,
)


def test_zero_di_zero_effective() -> None:
    assert effective_decline_first_year(0.0, 1.0) == pytest.approx(0.0)
    assert effective_decline_first_year(0.0) == pytest.approx(0.0)


def test_exponential_form_b_zero() -> None:
    # D_eff = 1 - exp(-Di)
    assert effective_decline_first_year(1.0) == pytest.approx(1.0 - math.exp(-1.0))
    assert effective_decline_first_year(0.85) == pytest.approx(1.0 - math.exp(-0.85))


def test_harmonic_form_b_one() -> None:
    # D_eff = Di / (1 + Di)
    for di in (0.5, 1.0, 2.0, 3.0):
        assert effective_decline_first_year(di, b=1.0) == pytest.approx(di / (1 + di))


def test_basin_cheatsheet_b_one() -> None:
    # 70% effective ↔ 2.33 nominal (per the cheat sheet in the docstring).
    nominal_for_70eff = nominal_from_effective(0.70, b=1.0)
    assert nominal_for_70eff == pytest.approx(0.70 / 0.30, rel=1e-6)
    # And it round-trips.
    assert effective_decline_first_year(nominal_for_70eff, b=1.0) == pytest.approx(0.70)


def test_round_trip_hyperbolic_b_one_two() -> None:
    for d_eff in (0.30, 0.50, 0.65, 0.78):
        nom = nominal_from_effective(d_eff, b=1.2)
        back = effective_decline_first_year(nom, b=1.2)
        assert back == pytest.approx(d_eff, rel=1e-6)


def test_effective_di_bounded_below_one() -> None:
    # No matter how huge nominal Di goes, effective is asymptotically 1.
    for nom in (5.0, 10.0, 100.0):
        d_eff = effective_decline_first_year(nom, b=1.0)
        assert 0 < d_eff < 1.0
