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
# Forecast horizon cap.
DEFAULT_FORECAST_HORIZON_YEARS: float = 50.0
# Economic-limit defaults are 0 — this tool is a TECHNICAL type-curve /
# decline generator and EUR is the raw 50-yr integral. Economics happens
# downstream on the exported workbook in the user's cash-flow tool.
# Callers that need the original rate-floor semantic (b ≥ 1 hyperbolic
# divergence guard, late-life ROI cutoff) can still pass an explicit
# positive value — see ``compute_eur(economic_limit=...)`` and the
# `test_eur.py` regression tests for that path.
DEFAULT_ECONOMIC_LIMIT_BOPD: float = 0.0
DEFAULT_ECONOMIC_LIMIT_MCFD: float = 0.0
DEFAULT_ECONOMIC_LIMIT_BWPD: float = 0.0

# Absolute rate floor for downtime filtering. A post-peak month whose
# calday rate is below this value gets flagged as downtime and excluded
# from the fit, regardless of how it compares to the local rolling-max.
# Handles the choppy-shut-in pattern where a well bounces between zero
# and a low residual rate — the rolling-max check alone gets dragged
# down by the zero neighbors and lets the residual-rate months through.
# Permian-tuned: oil at 5 BOPD is well below any genuine producing
# baseline; gas at 30 MCFD and water at 10 BWPD likewise.
DEFAULT_DOWNTIME_FLOOR_BOPD: float = 5.0
DEFAULT_DOWNTIME_FLOOR_MCFD: float = 30.0
DEFAULT_DOWNTIME_FLOOR_BWPD: float = 10.0

# Water-specific nominal-Di upper bound. Permian flowback water craters in
# the first months far faster than oil declines, so the oil-tuned cap
# (fit.DI_NOMINAL_HI_PER_YEAR = 4.0) is too low for water and forces the
# rate-time water fit to pin short of the true decline. 12.0/yr nominal is
# ~92% effective in year 1 at b=1 — covers steep Permian water without
# letting a degenerate short-history fit run away. Tunable; oil and gas
# keep the 4.0 cap. See fit.fit_with_fallback's qi-underfit path.
DEFAULT_WATER_DI_NOMINAL_HI_PER_YEAR: float = 12.0


@dataclass(frozen=True)
class ForecastConfig:
    """Per-fit knobs the orchestrator can override.

    The brief calls for `modified_hyperbolic` + `fit_rate_cum` defaults,
    with the rate-time fit available as a per-well toggle and the model
    switchable in the detail modal.
    """

    model_type: str = "modified_hyperbolic"
    fit_method: str = "rate_cum"
    # Terminal (exponential-tail) decline. df_terminal_per_year is the
    # Delaware / default value; Midland wells use df_terminal_midland.
    # The orchestrator picks per well from wells.subbasin — Midland's
    # boundary-dominated tails warrant a shallower terminal.
    df_terminal_per_year: float = DEFAULT_DF_TERMINAL_PER_YEAR
    df_terminal_midland: float = 0.06
    horizon_years: float = DEFAULT_FORECAST_HORIZON_YEARS
    economic_limit_bopd: float = DEFAULT_ECONOMIC_LIMIT_BOPD
    economic_limit_mcfd: float = DEFAULT_ECONOMIC_LIMIT_MCFD
    economic_limit_bwpd: float = DEFAULT_ECONOMIC_LIMIT_BWPD
    # Absolute rate below which a post-peak month gets dropped as
    # downtime, regardless of its relationship to the rolling-max.
    # Catches the choppy-restart pattern that slips through the 30%-
    # of-local-max filter alone.
    downtime_floor_bopd: float = DEFAULT_DOWNTIME_FLOOR_BOPD
    downtime_floor_mcfd: float = DEFAULT_DOWNTIME_FLOOR_MCFD
    downtime_floor_bwpd: float = DEFAULT_DOWNTIME_FLOOR_BWPD
    # Nominal-Di upper bound for the water fit (oil/gas use the oil-tuned
    # fit.DI_NOMINAL_HI_PER_YEAR). Water craters faster early; see above.
    water_di_nominal_hi_per_year: float = DEFAULT_WATER_DI_NOMINAL_HI_PER_YEAR
    # Peak-anchor qi (ON by default): the fit's qi is constrained to
    # [lo, hi] * observed_peak_rate instead of the wide [0, 10*peak]
    # default. Anchoring qi near the peak stops the cum fit from settling
    # into the coupled low-qi / low-Di degenerate corner — diagnostics on
    # braveheart_wca showed corr(qi_capture, di_gap)=+0.71 on oil, which
    # this drops to ~+0.12 (qi<0.80: 30%->0%, shallow-Di: 18%->1%).
    # Applies to OIL and WATER only — gas inherits the oil peak, so its
    # peak_rate is the oil rate and anchoring would use the wrong scale
    # (see fit._qi_bounds). Set either to None to disable.
    qi_anchor_lo_frac: float | None = 0.90
    qi_anchor_hi_frac: float | None = 1.05
    # Override the hyperbolic-b upper bound (default fit.B_HI = 1.2, a
    # deliberately tight Wolfcamp cap). Raising it allows fatter tails —
    # diagnostics show the model under-predicts the out-year tail / cum by
    # ~10%, which a higher b can lift. None = use the module default.
    b_nominal_hi: float | None = None
    # Override the hyperbolic-b LOWER bound (default fit.B_LO = 0.9).
    # Because the cum fit is nearly insensitive to b, raising the ceiling
    # doesn't fatten tails — only forcing b up via the floor does. None =
    # module default.
    b_nominal_lo: float | None = None
    # >= 6 months post-peak required for the default fit (brief).
    min_post_peak_months: int = 6
    # Wells with fewer than this many post-peak months are excluded from
    # the autoforecast batch and surfaced as pending-transfer. The user
    # reviews the long-history fits, then runs the transfer endpoint to
    # write forecasts for the short-history wells using the long
    # cohort's median Di / b. Setting this to None disables the
    # partition entirely — every well is fit unconstrained (legacy
    # behavior, preserved for direct API callers and tests).
    short_history_cutoff_months: int | None = None


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
    # Fraction of post-peak months excluded as downtime (0.0 to 1.0).
    # See app.forecasting.fit::_flag_downtime for the heuristic.
    downtime_ratio: float = 0.0
    # Linear-ramp prefix carried alongside the Arps fit. qo is the
    # stream's first-prod-month rate; peak_index_months is the count
    # of months from first prod to the detected peak. The forecast
    # model is ramp(qo→qi over peak_index_months) + Arps. Both are
    # None when there's no first-prod observation to anchor qo or
    # when peak_index_months is 0 — evaluator falls back to pure Arps.
    qo: float | None = None
    peak_index_months: int | None = None

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
            "downtime_ratio": self.downtime_ratio,
            "qo": self.qo,
            "peak_index_months": self.peak_index_months,
        }
