"""Downtime-filter behavior.

Pins the OR-semantic between the two downtime rules:
  1. Rolling-max relative: rate < 30% of the centered 7-month max.
  2. Absolute floor: rate < downtime_floor.

The motivating real-well case (GOODNIGHT 10/11 A B201MD on 2026-05-29):
peak ~800 BOPD, then the well bounces between zero and a residual
~200 BOPD for the next two years. The relative-only filter let the
~200 BOPD months through because the centered max stayed near 200
once the well stopped producing at full rate, dragging the threshold
down. An absolute 5-BOPD floor changes nothing for those months but
also doesn't help — that case wanted the floor to be more like the
residual rate, not a true "near zero" floor. So the absolute floor's
real job is the SIMPLER case: a month at 0–4 BOPD that the relative
filter would catch in clean wells, but lets through when the rolling
max happens to be tiny (late-life tails, intermittent producers).
"""

from __future__ import annotations

import pandas as pd

from app.forecasting.fit import _flag_downtime


def test_relative_rule_alone_with_floor_zero() -> None:
    # 0 BOPD next to 800 BOPD neighbors → relative test fires
    # (0 < 0.3 * 800 = 240) regardless of the absolute floor.
    rates = pd.Series([800, 0, 750, 700, 650, 600, 550], dtype=float)
    flags = _flag_downtime(rates, absolute_floor=0.0)
    assert flags.iloc[1]
    assert not flags.iloc[0]
    assert not flags.iloc[2]


def test_absolute_floor_catches_subfloor_when_local_max_tiny() -> None:
    # All-low series — local max is ~3 BOPD, so the relative
    # threshold collapses to its 1.0 floor. The zero months flag
    # under the relative rule (0 < 1.0); the 3.0 months don't
    # (3.0 >/= 1.0). The absolute 5.0-BOPD floor catches the 3.0s.
    rates = pd.Series([3.0, 0.0, 3.0, 0.0, 3.0, 0.0, 3.0], dtype=float)
    rel_only = _flag_downtime(rates, absolute_floor=0.0)
    with_floor = _flag_downtime(rates, absolute_floor=5.0)
    assert not rel_only.iloc[0]  # 3.0 NOT flagged by relative-only
    assert rel_only.iloc[1]  # 0.0 flagged by relative-only
    assert with_floor.all()  # every value < 5.0 → all flagged


def test_absolute_floor_does_not_flag_genuine_production() -> None:
    # Normal Permian-like decline series — every value is above the
    # 5-BOPD floor, and the relative test sees a smooth slope so
    # nothing should flag.
    rates = pd.Series([800, 720, 660, 600, 550, 500, 450, 400, 360, 320, 290], dtype=float)
    flags = _flag_downtime(rates, absolute_floor=5.0)
    assert not flags.any()


def test_absolute_floor_zero_disables_absolute_leg() -> None:
    # Confirm backward-compat: passing 0.0 (the default for callers
    # that don't opt in) reduces to the pre-floor behavior.
    rates = pd.Series([100, 2.0, 100, 2.0, 100], dtype=float)
    flags_no_floor = _flag_downtime(rates, absolute_floor=0.0)
    # 2.0 < 0.3 * 100 = 30 → relative test catches them regardless.
    assert flags_no_floor.iloc[1]
    assert flags_no_floor.iloc[3]


def test_or_semantic_with_mixed_signals() -> None:
    # Smoke-test the OR: producing neighbors keep the relative
    # threshold high; the floor at 5.0 sweeps up everything below it.
    # No row-level reasoning about WHICH rule caught a given month —
    # both might fire on the same one. We just assert the outcome.
    rates = pd.Series([800, 5.0, 750, 700, 3.0, 4.0, 3.0], dtype=float)
    flags = _flag_downtime(rates, absolute_floor=5.0)
    assert flags.iloc[1]
    assert flags.iloc[4]
    assert flags.iloc[5]
    assert flags.iloc[6]
    assert not flags.iloc[0]
    assert not flags.iloc[2]
    assert not flags.iloc[3]
