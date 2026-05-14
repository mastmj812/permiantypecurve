"""Forecasting types + tuning constants.

Time unit convention throughout the forecasting package: **years**.
Decline parameters are nominal per-year (e.g. Di = 0.85 /yr means an
85%/yr nominal decline). Rates are per-day (BOPD / MCFD / BWPD), the same
units as `production_monthly.rate_calday_*`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Per the brief: modified-hyperbolic terminal decline defaults to 8%/yr.
DEFAULT_DF_TERMINAL_PER_YEAR: float = 0.08
# Forecast horizon cap (still subject to economic-limit cutoff per-stream).
DEFAULT_FORECAST_HORIZON_YEARS: float = 50.0
# Oil economic-limit default — user-adjustable in the detail modal.
DEFAULT_ECONOMIC_LIMIT_BOPD: float = 5.0
# Per-stream economic limits used during EUR integration.
DEFAULT_ECONOMIC_LIMIT_MCFD: float = 30.0  # 5 BOPD × 6 mcf/bbl
DEFAULT_ECONOMIC_LIMIT_BWPD: float = 50.0  # generous; water rarely binds


@dataclass(frozen=True)
class ForecastConfig:
    """Per-fit knobs the orchestrator can override.

    The brief calls for `modified_hyperbolic` + `fit_rate_cum` defaults,
    with the rate-time fit available as a per-well toggle and the model
    switchable in the detail modal.
    """

    model_type: str = "modified_hyperbolic"
    fit_method: str = "rate_cum"
    df_terminal_per_year: float = DEFAULT_DF_TERMINAL_PER_YEAR
    horizon_years: float = DEFAULT_FORECAST_HORIZON_YEARS
    economic_limit_bopd: float = DEFAULT_ECONOMIC_LIMIT_BOPD
    economic_limit_mcfd: float = DEFAULT_ECONOMIC_LIMIT_MCFD
    economic_limit_bwpd: float = DEFAULT_ECONOMIC_LIMIT_BWPD
    # >= 6 months post-peak required for the default fit (brief).
    min_post_peak_months: int = 6


@dataclass(frozen=True)
class ForecastResult:
    """The return value of fit_rate_cum / fit_rate_time."""

    model_type: str
    params: dict[str, float]
    qi: float
    di_initial: float | None
    b: float | None
    df_terminal: float | None
    eur: float
    peak_month_date: date | None
    peak_rate: float
    fit_method: str
    fit_r2: float
    fit_rmse: float
    n_points_fit: int
    insufficient_history: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "params": self.params,
            "qi": self.qi,
            "di_initial": self.di_initial,
            "b": self.b,
            "df_terminal": self.df_terminal,
            "eur": self.eur,
            "peak_month_date": (
                self.peak_month_date.isoformat() if self.peak_month_date else None
            ),
            "peak_rate": self.peak_rate,
            "fit_method": self.fit_method,
            "fit_r2": self.fit_r2,
            "fit_rmse": self.fit_rmse,
            "n_points_fit": self.n_points_fit,
            "insufficient_history": self.insufficient_history,
            "notes": self.notes,
        }
