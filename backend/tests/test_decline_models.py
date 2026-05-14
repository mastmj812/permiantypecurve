"""Known-value tests for the five decline-curve models.

For each model, we hand-derive a value at a specific (qi, Di, b, t) and
assert the implementation matches. This catches sign errors, exponent
inversions, and similar off-by-one bugs that synthetic-data fitting would
miss because the fitter compensates.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.forecasting.models import (
    arps_exponential,
    arps_harmonic,
    arps_hyperbolic,
    duong,
    modified_hyperbolic,
    switchover_time,
)


def test_arps_exponential_at_t_zero_equals_qi() -> None:
    assert arps_exponential(0.0, qi=500.0, Di=0.85) == pytest.approx(500.0)


def test_arps_exponential_at_one_time_constant_decays_by_1_over_e() -> None:
    # q(1/Di) = qi / e
    q = arps_exponential(1.0 / 0.85, qi=500.0, Di=0.85)
    assert q == pytest.approx(500.0 / math.e, rel=1e-6)


def test_arps_exponential_vectorized() -> None:
    t = np.array([0.0, 0.5, 1.0, 2.0])
    q = arps_exponential(t, qi=1000.0, Di=1.0)
    assert q[0] == pytest.approx(1000.0)
    assert q[2] == pytest.approx(1000.0 / math.e, rel=1e-6)


def test_arps_hyperbolic_at_t_zero_equals_qi() -> None:
    assert arps_hyperbolic(0.0, qi=500.0, Di=0.85, b=1.1) == pytest.approx(500.0)


def test_arps_hyperbolic_b_zero_collapses_to_exponential() -> None:
    t = np.array([0.5, 1.0, 2.0])
    hyp = arps_hyperbolic(t, qi=500.0, Di=0.85, b=1e-9)
    expo = arps_exponential(t, qi=500.0, Di=0.85)
    assert hyp == pytest.approx(expo, rel=1e-6)


def test_arps_hyperbolic_known_value() -> None:
    # q(1) = qi / (1 + b*Di*1)^(1/b) for qi=1000, Di=0.5, b=1.0
    # = 1000 / (1 + 0.5)^1 = 666.667
    q = arps_hyperbolic(1.0, qi=1000.0, Di=0.5, b=1.0)
    assert q == pytest.approx(1000.0 / 1.5, rel=1e-6)


def test_arps_harmonic_known_value() -> None:
    # q(2) = qi / (1 + Di*2) for qi=400, Di=0.25 → 400 / 1.5 = 266.67
    q = arps_harmonic(2.0, qi=400.0, Di=0.25)
    assert q == pytest.approx(400.0 / 1.5, rel=1e-6)


def test_arps_harmonic_equals_hyperbolic_b_one() -> None:
    t = np.array([0.5, 1.0, 3.0])
    harm = arps_harmonic(t, qi=750.0, Di=0.6)
    hyp = arps_hyperbolic(t, qi=750.0, Di=0.6, b=1.0)
    assert harm == pytest.approx(hyp, rel=1e-9)


def test_modified_hyperbolic_continuous_at_switchover() -> None:
    # The piecewise switchover MUST be continuous — left and right limits agree.
    qi, Di, b, Df = 800.0, 0.9, 1.2, 0.08
    t_s = switchover_time(Di, Df, b)
    # Approach from below and above the switchover by 1 hour (≈1e-4 yr).
    left = modified_hyperbolic(t_s - 1e-4, qi, Di, b, Df)
    right = modified_hyperbolic(t_s + 1e-4, qi, Di, b, Df)
    assert left == pytest.approx(right, rel=1e-3)


def test_modified_hyperbolic_hyperbolic_phase_matches_arps_hyperbolic() -> None:
    qi, Di, b, Df = 800.0, 0.9, 1.2, 0.08
    t_s = switchover_time(Di, Df, b)
    # Sample before switchover — should equal pure hyperbolic.
    t = np.array([0.1, 0.5, min(1.0, t_s * 0.9)])
    mh = modified_hyperbolic(t, qi, Di, b, Df)
    hyp = arps_hyperbolic(t, qi, Di, b)
    assert mh == pytest.approx(hyp, rel=1e-9)


def test_modified_hyperbolic_exp_phase_decays_at_df() -> None:
    qi, Di, b, Df = 800.0, 0.9, 1.2, 0.08
    t_s = switchover_time(Di, Df, b)
    q_s = qi * (Df / Di) ** (1.0 / b)
    # 1 year after switchover, rate should be q_s * exp(-Df * 1)
    q_observed = modified_hyperbolic(t_s + 1.0, qi, Di, b, Df)
    assert q_observed == pytest.approx(q_s * math.exp(-Df), rel=1e-6)


def test_modified_hyperbolic_no_switchover_when_df_ge_di() -> None:
    # With Df >= Di there's no switchover; behaves as pure hyperbolic.
    qi, Di, b, Df = 800.0, 0.1, 1.2, 0.5  # Df > Di
    t = np.array([1.0, 5.0, 10.0])
    mh = modified_hyperbolic(t, qi, Di, b, Df)
    hyp = arps_hyperbolic(t, qi, Di, b)
    assert mh == pytest.approx(hyp, rel=1e-9)


def test_switchover_time_formula() -> None:
    # D(t_s) = Di / (1 + b*Di*t_s) = Df  →  t_s = (Di/Df - 1) / (b*Di)
    Di, Df, b = 0.9, 0.08, 1.2
    expected = (Di / Df - 1.0) / (b * Di)
    assert switchover_time(Di, Df, b) == pytest.approx(expected, rel=1e-9)


def test_duong_at_t_one_equals_q1() -> None:
    # By definition q(t=1) = q1 * 1^(-m) * exp(0) = q1
    q = duong(1.0, q1=600.0, m=1.2, a=0.4)
    assert q == pytest.approx(600.0, rel=1e-9)


def test_duong_monotone_decreasing_for_typical_params() -> None:
    # m > 1, a > 0 → strictly decreasing past t=1.
    t = np.array([1.0, 2.0, 4.0, 8.0])
    q = duong(t, q1=600.0, m=1.2, a=0.4)
    diffs = np.diff(np.asarray(q))
    assert np.all(diffs < 0)
