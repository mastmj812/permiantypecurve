"""Cohort-level terminal Df policy for the type-curve P50 fit.

The cohort's P50 is a single aggregate series, so one terminal Df is
imposed. Policy of record: Midland's shallower df_terminal_midland (0.06)
when Midland is the MAJORITY (> 50%) of the aggregated wells, else the
Delaware/default df_terminal_per_year (0.08). NULL/blank subbasins count
toward the total but never as Midland.
"""

from __future__ import annotations

from app.forecasting.orchestrator import df_terminal_for_cohort
from app.forecasting.types import ForecastConfig

_CFG = ForecastConfig()  # df_terminal_per_year=0.08, df_terminal_midland=0.06


def test_majority_midland_uses_shallower_df() -> None:
    subbasins = ["Midland", "Midland", "Midland", "Delaware"]  # 3/4 = 75%
    assert df_terminal_for_cohort(subbasins, _CFG) == _CFG.df_terminal_midland


def test_exactly_half_midland_is_not_majority() -> None:
    # 2/4 = 50% is NOT > 50% → keep the default (deliberately conservative
    # about shallowing the tail, which raises EUR).
    subbasins = ["Midland", "Midland", "Delaware", "Delaware"]
    assert df_terminal_for_cohort(subbasins, _CFG) == _CFG.df_terminal_per_year


def test_plurality_but_not_majority_uses_default() -> None:
    # 2 Midland / 1 Delaware / 2 null = 2/5 = 40% Midland → default.
    subbasins = ["Midland", "Midland", "Delaware", None, None]
    assert df_terminal_for_cohort(subbasins, _CFG) == _CFG.df_terminal_per_year


def test_nulls_count_toward_total() -> None:
    # 2 Midland + 3 null = 40% → default; 3 Midland + 2 null = 60% → midland.
    assert (
        df_terminal_for_cohort(["Midland", "Midland", None, None, None], _CFG)
        == _CFG.df_terminal_per_year
    )
    assert (
        df_terminal_for_cohort(["Midland", "Midland", "Midland", None, None], _CFG)
        == _CFG.df_terminal_midland
    )


def test_case_and_whitespace_insensitive() -> None:
    subbasins = ["  midland ", "MIDLAND", "Delaware"]  # 2/3 = 67%
    assert df_terminal_for_cohort(subbasins, _CFG) == _CFG.df_terminal_midland


def test_all_delaware_uses_default() -> None:
    assert (
        df_terminal_for_cohort(["Delaware", "Delaware"], _CFG)
        == _CFG.df_terminal_per_year
    )


def test_empty_cohort_uses_default() -> None:
    assert df_terminal_for_cohort([], _CFG) == _CFG.df_terminal_per_year
