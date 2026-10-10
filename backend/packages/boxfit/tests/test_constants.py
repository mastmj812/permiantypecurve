"""Domain constants of record (CLAUDE.md rules 1, 6; sub-basin Df). A change
here is a decision, not a refactor — it must arrive with Michael's say-so."""

from __future__ import annotations

from boxfit import fit
from boxfit.ramp_arps import DISPLAY_EUR_N_MONTHS
from boxfit.types import (
    DEFAULT_DF_TERMINAL_PER_YEAR,
    DEFAULT_FORECAST_HORIZON_YEARS,
    DEFAULT_WATER_DI_NOMINAL_HI_PER_YEAR,
    ForecastConfig,
)
from boxfit.well import df_terminal_for_cohort, df_terminal_for_subbasin


def test_nominal_di_and_b_bounds() -> None:
    assert (fit.DI_NOMINAL_LO_PER_YEAR, fit.DI_NOMINAL_HI_PER_YEAR) == (0.5, 4.0)
    assert (fit.B_LO, fit.B_HI) == (0.9, 1.2)
    assert DEFAULT_WATER_DI_NOMINAL_HI_PER_YEAR == 12.0
    assert ForecastConfig().water_di_nominal_hi_per_year == 12.0


def test_terminal_decline_and_horizon() -> None:
    cfg = ForecastConfig()
    assert DEFAULT_DF_TERMINAL_PER_YEAR == 0.08
    assert cfg.df_terminal_midland == 0.06
    assert df_terminal_for_subbasin("Midland", cfg) == 0.06
    assert df_terminal_for_subbasin("DELAWARE", cfg) == 0.08
    assert df_terminal_for_subbasin(None, cfg) == 0.08
    # Cohort: strict Midland majority only; NULLs count against.
    assert df_terminal_for_cohort(["MIDLAND", "MIDLAND", None], cfg) == 0.06
    assert df_terminal_for_cohort(["MIDLAND", None], cfg) == 0.08
    assert DEFAULT_FORECAST_HORIZON_YEARS == 50.0
    assert DISPLAY_EUR_N_MONTHS == 600
