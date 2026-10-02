"""Daily-production PDP forecasting (seller VDR daily volumes).

Pure functions — no DB. The deal-scoped service (``app.pdp.service``)
loads ``vdr_daily_production`` rows, calls these, and persists to
``pdp_forecasts``. Kept separate from the monthly engine on purpose: the
global ``forecasts`` table stays on the warehouse (Novi monthly) basis so
type-curve analog cohorts never silently mix data bases.

Method of record (decided 2026-10-02 on the alchemist daily data):

* **Producing-day fit x uptime.** A stream's day is DOWN when its volume
  is missing, negative, below the stream's absolute floor, or below 30%
  of the centered 31-day rolling max (the monthly ``_flag_downtime``
  rule at day grain). The well is down when oil AND gas are both down.
  The fit uses producing days only; the forecast is multiplied by a
  per-well uptime factor to get calendar volumes.
* **Uptime = routine downtime only.** Trailing 365 days; runs of >= 14
  consecutive well-down days are shut-in EVENTS (offset fracs,
  workovers) — excluded from the uptime ratio and reported as breaks.
* **Log-rate residuals.** Daily noise is roughly proportional to rate;
  linear residuals let the high-rate early life dominate and left the
  mature tails 13-21% high on alchemist, log residuals 6-7%.
* **Time origin = the stream's OWN peak**, always. An engineer fit
  window (``fit_start``) only selects which days are fitted — resetting
  the clock to the window start would make the house Di bounds (which
  are from-peak bounds) pin at the 0.5/yr floor. In a window, qi is
  free (it is a virtual peak rate, no longer anchored to the observed
  peak).
* **Unpeaked streams** (smoothed peak inside the last 60 days of data,
  or < 90 producing days after it — managed-choke new wells) are not
  fitted: they decline NOW from the trailing-30-day producing-day rate
  with Di from the same-bench warehouse cohort and b from the bench
  prior (``transfer_now``). No credit for further incline.

Bounds, Arps forms and the b prior are the monthly engine's own
(``app.forecasting.fit``). Rates are per producing day (BOPD / MCFD /
BWPD); Di is NOMINAL per year; t is years = days / 365.25.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from app.forecasting.fit import (
    B_HI,
    B_LO,
    _bounds_and_p0,
    _fit_with_b_prior,
    _rate_callable,
    _stream_di_hi,
    detect_at_bound,
)
from app.forecasting.metrics import effective_decline_first_year
from app.forecasting.models import modified_hyperbolic
from app.forecasting.types import ForecastConfig

DAYS_PER_YEAR_CAL: float = 365.25
MODEL_TYPE: str = "modified_hyperbolic"

STREAMS: tuple[str, ...] = ("oil", "gas", "water")
VOLUME_COL: dict[str, str] = {"oil": "oil_bbl", "gas": "gas_mcf", "water": "water_bbl"}

DOWN_WINDOW_DAYS: int = 31
DOWN_RELATIVE: float = 0.30
EVENT_MIN_DAYS: int = 14
SMOOTH_DAYS: int = 7
PEAK_SEARCH_DAYS: int = 365
UNPEAKED_RECENT_DAYS: int = 60
MIN_POST_PEAK_DAYS: int = 90
UPTIME_WINDOW_DAYS: int = 365
CURRENT_RATE_DAYS: int = 30
TAIL_DAYS: int = 90
TAIL_TOLERANCE: float = 0.15
RECENT_BREAK_DAYS: int = 548
# Choke changes this early are the managed-choke ramp, not operational
# breaks: recorded (they belong on the review chart) but never flagged.
EARLY_LIFE_DAYS: int = 180
# A choke change is MATERIAL when the oil producing-day rate steps by at
# least this much across it (or the step can't be measured). Operators
# trim chokes constantly (alchemist Atlanta 73H: 11 adjustments, most
# < 10% rate change); only material ones are operational breaks.
MATERIAL_RATE_STEP: float = 0.15
# No producing day in this many trailing days -> the stream is SHUT IN
# (seller PDNP / inactive): forecast zero unless the engineer sets a
# restart (manual params with a future anchor). Without this rule the
# whole idle year is one shut-in EVENT (excluded from uptime -> 1.0) and
# a from-peak fit would put phantom volumes on a dead well (alchemist
# Atlanta 73 2H: nothing real since 2024; seller's own forecast is zero).
SHUT_IN_DAYS: int = 365
CHOKE_PERSIST_READINGS: int = 7
RATE_STEP_WINDOW: int = 14


def stream_floor(stream: str, config: ForecastConfig) -> float:
    return float(
        {
            "oil": config.downtime_floor_bopd,
            "gas": config.downtime_floor_mcfd,
            "water": config.downtime_floor_bwpd,
        }[stream]
    )


# ---------------------------------------------------------------------------
# Preparation: calendar, down masks, events
# ---------------------------------------------------------------------------


def stream_down(q: pd.Series, floor: float) -> pd.Series:
    """True where the stream did not produce normally that day."""
    clean = q.clip(lower=0).fillna(0.0)
    local_max = clean.rolling(DOWN_WINDOW_DAYS, center=True, min_periods=1).max()
    relative = (DOWN_RELATIVE * local_max).clip(lower=1.0)
    return q.isna() | (q < 0) | (q < floor) | (q < relative)


def true_runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True runs."""
    out: list[tuple[int, int]] = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def prepare_daily(df: pd.DataFrame, config: ForecastConfig | None = None) -> pd.DataFrame:
    """One row per calendar day from first to last report, with down flags.

    Input columns: ``prod_date`` + oil_bbl / gas_mcf / water_bbl, optional
    choke / tbg_psi / csg_psi. Missing calendar days become NaN rows (a
    day with no report is a down day). ``choke == 0`` is treated as
    MISSING, not shut — sellers zero the column when the reading lapses
    (alchemist Lowe 72H reads 0 from 2026-06 while flowing at 880 psi).
    """
    cfg = config or ForecastConfig()
    d = df.copy()
    d["prod_date"] = pd.to_datetime(d["prod_date"]).dt.normalize()
    d = d.sort_values("prod_date").drop_duplicates("prod_date", keep="last")
    full = pd.date_range(d["prod_date"].min(), d["prod_date"].max(), freq="D")
    d = d.set_index("prod_date").reindex(full)
    d.index.name = "prod_date"
    d = d.reset_index()
    for col in ("oil_bbl", "gas_mcf", "water_bbl", "choke", "tbg_psi", "csg_psi"):
        if col not in d:
            d[col] = np.nan
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d.loc[d["choke"] <= 0, "choke"] = np.nan
    for s in STREAMS:
        d[f"{s}_down"] = stream_down(d[VOLUME_COL[s]], stream_floor(s, cfg))
    d["well_down"] = d["oil_down"] & d["gas_down"]
    # Down days before the first producing day are PRE-PRODUCTION (the
    # report started before first sales), not a shut-in: no event, no
    # uptime penalty.
    up = np.flatnonzero(~d["well_down"].to_numpy())
    pre = np.zeros(len(d), dtype=bool)
    pre[: up[0] if len(up) else len(d)] = True
    d["pre_production"] = pre
    event = np.zeros(len(d), dtype=bool)
    for a, b in true_runs(d["well_down"].to_numpy() & ~pre):
        if b - a >= EVENT_MIN_DAYS:
            event[a:b] = True
    d["event_down"] = event
    return d


# ---------------------------------------------------------------------------
# Uptime + breaks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Uptime:
    factor: float
    window_start: date
    window_end: date
    calendar_days: int
    event_days: int
    routine_down_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": round(self.factor, 4),
            "basis": "routine_downtime_trailing_365d_excl_events_ge_14d",
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "calendar_days": self.calendar_days,
            "event_days": self.event_days,
            "routine_down_days": self.routine_down_days,
        }


def compute_uptime(prep: pd.DataFrame, window_days: int = UPTIME_WINDOW_DAYS) -> Uptime:
    end = prep["prod_date"].max()
    w = prep[(prep["prod_date"] > end - pd.Timedelta(days=window_days)) & ~prep["pre_production"]]
    events = int(w["event_down"].sum())
    routine = int((w["well_down"] & ~w["event_down"]).sum())
    denom = len(w) - events
    factor = 1.0 if denom <= 0 else 1.0 - routine / denom
    return Uptime(
        factor=float(factor),
        window_start=w["prod_date"].min().date(),
        window_end=end.date(),
        calendar_days=len(w),
        event_days=events,
        routine_down_days=routine,
    )


def _median_producing(series: pd.Series, down: pd.Series) -> float | None:
    vals = series[~down]
    return float(vals.median()) if len(vals) else None


def detect_breaks(prep: pd.DataFrame) -> list[dict[str, Any]]:
    """Shut-in events (>= 14 d) and persistent choke changes, with the
    oil producing-day rate step across each. Flags only — never moves a
    fit window by itself (decision C)."""
    out: list[dict[str, Any]] = []
    oil, down = prep["oil_bbl"], prep["oil_down"]
    for a, b in true_runs(prep["event_down"].to_numpy()):
        pre = _median_producing(oil.iloc[max(0, a - 30) : a], down.iloc[max(0, a - 30) : a])
        lo, hi = b + 7, b + 37
        post = _median_producing(oil.iloc[lo:hi], down.iloc[lo:hi])
        out.append(
            {
                "kind": "shut_in",
                "start": prep["prod_date"].iloc[a].date().isoformat(),
                "end": prep["prod_date"].iloc[b - 1].date().isoformat(),
                "days": b - a,
                "oil_rate_before": None if pre is None else round(pre, 1),
                "oil_rate_after": None if post is None else round(post, 1),
                "rate_ratio": round(post / pre, 3) if pre and post else None,
            }
        )
    producing = prep.loc[~prep["pre_production"], "prod_date"]
    first_prod = producing.min() if len(producing) else prep["prod_date"].min()
    readings = prep.loc[prep["choke"].notna(), ["prod_date", "choke"]].reset_index()
    vals = readings["choke"].to_numpy()
    i = 1
    while i < len(vals):
        if vals[i] != vals[i - 1]:
            after = vals[i : i + CHOKE_PERSIST_READINGS]
            if len(after) == CHOKE_PERSIST_READINGS and np.all(after == vals[i]):
                at = int(readings["index"].iloc[i])
                pre = _median_producing(
                    oil.iloc[max(0, at - RATE_STEP_WINDOW) : at],
                    down.iloc[max(0, at - RATE_STEP_WINDOW) : at],
                )
                post = _median_producing(
                    oil.iloc[at : at + RATE_STEP_WINDOW], down.iloc[at : at + RATE_STEP_WINDOW]
                )
                out.append(
                    {
                        "kind": "choke_change",
                        "start": readings["prod_date"].iloc[i].date().isoformat(),
                        "early_life": bool(
                            (readings["prod_date"].iloc[i] - first_prod).days < EARLY_LIFE_DAYS
                        ),
                        "choke_from": float(vals[i - 1]),
                        "choke_to": float(vals[i]),
                        "oil_rate_before": None if pre is None else round(pre, 1),
                        "oil_rate_after": None if post is None else round(post, 1),
                        "rate_ratio": round(post / pre, 3) if pre and post else None,
                        "material": not (pre and post)
                        or abs(post / pre - 1.0) >= MATERIAL_RATE_STEP,
                    }
                )
                i += CHOKE_PERSIST_READINGS
                continue
        i += 1
    return sorted(out, key=lambda r: r["start"])


# ---------------------------------------------------------------------------
# Peak + classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyPeak:
    peak_date: date
    peak_rate: float  # smoothed producing-day rate at the peak
    first_producing: date


def producing_series(prep: pd.DataFrame, stream: str) -> pd.DataFrame:
    """Producing days of one stream: prod_date + q (per producing day)."""
    keep = ~prep[f"{stream}_down"] & ~prep["well_down"]
    out = prep.loc[keep, ["prod_date", VOLUME_COL[stream]]].rename(
        columns={VOLUME_COL[stream]: "q"}
    )
    return out.reset_index(drop=True)


def detect_daily_peak(prep: pd.DataFrame, stream: str) -> DailyPeak | None:
    """Max of the 7-day centered rolling MEDIAN of producing-day rate,
    searched over the first 365 days from the stream's first producing
    day. Ties break to the highest actual rate (house peak rule)."""
    p = producing_series(prep, stream)
    if p.empty:
        return None
    sm = p["q"].rolling(SMOOTH_DAYS, center=True, min_periods=1).median()
    first = p["prod_date"].iloc[0]
    window = p["prod_date"] <= first + pd.Timedelta(days=PEAK_SEARCH_DAYS)
    cand = sm[window]
    top = cand.max()
    if not np.isfinite(top) or top <= 0:
        return None
    tied = cand.index[cand >= top - 1e-9]
    best = max(tied, key=lambda i: p["q"].iloc[i])
    return DailyPeak(
        peak_date=p["prod_date"].iloc[best].date(),
        peak_rate=float(top),
        first_producing=first.date(),
    )


def classify_stream(prep: pd.DataFrame, stream: str, peak: DailyPeak | None) -> str:
    """``no_production`` | ``shut_in`` | ``unpeaked`` | ``fit``."""
    if peak is None:
        return "no_production"
    last = prep["prod_date"].max().date()
    recent = producing_series(prep, stream)
    if not (recent["prod_date"].dt.date > last - timedelta(days=SHUT_IN_DAYS)).any():
        return "shut_in"
    if (last - peak.peak_date).days < UNPEAKED_RECENT_DAYS:
        return "unpeaked"
    p = producing_series(prep, stream)
    post = int((p["prod_date"].dt.date >= peak.peak_date).sum())
    if post < MIN_POST_PEAK_DAYS:
        return "unpeaked"
    return "fit"


def current_rate(prep: pd.DataFrame, stream: str, days: int = CURRENT_RATE_DAYS) -> float | None:
    """Median producing-day rate over the trailing ``days`` calendar days."""
    p = producing_series(prep, stream)
    end = prep["prod_date"].max()
    recent = p.loc[p["prod_date"] > end - pd.Timedelta(days=days), "q"]
    return float(recent.median()) if len(recent) else None


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyForecast:
    """One stream's PDP forecast. ``anchor_date`` is t = 0 for ``params``."""

    stream: str
    method: str  # daily_fit | transfer_now
    params: dict[str, float]
    anchor_date: date
    data_through: date
    fit_start: date | None = None
    peak_rate: float | None = None
    fit_r2_log: float | None = None
    fit_rmse_log: float | None = None
    n_points: int = 0
    at_bound: str | None = None
    tail_ratio: float | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def qi(self) -> float:
        return self.params["qi"]

    @property
    def di(self) -> float:
        return self.params["Di"]

    @property
    def b(self) -> float:
        return self.params["b"]


def _t_years(dates: Any, anchor: date) -> NDArray[np.float64]:
    days = (pd.DatetimeIndex(pd.to_datetime(dates)) - pd.Timestamp(anchor)).days
    return (np.asarray(days, dtype=float) + 0.5) / DAYS_PER_YEAR_CAL


def model_rate(params: dict[str, float], anchor: date, dates: Any) -> NDArray[np.float64]:
    """Producing-day rate on ``dates`` (t = 0 at ``anchor``). Zero BEFORE
    the anchor: a restart forecast (manual params, future anchor) must not
    produce between data_through and the restart date."""
    t_raw = _t_years(dates, anchor)
    rate = np.asarray(
        modified_hyperbolic(
            np.maximum(t_raw, 0.0), params["qi"], params["Di"], params["b"], params["Df"]
        ),
        dtype=float,
    )
    return np.where(t_raw < 0, 0.0, rate)


def tail_ratio(
    prep: pd.DataFrame, stream: str, params: dict[str, float], anchor: date
) -> float | None:
    """Mean model / mean actual producing-day rate over the last 90 days
    (only producing days at or after the anchor). The review signal."""
    p = producing_series(prep, stream)
    end = prep["prod_date"].max()
    tail = p[
        (p["prod_date"] > end - pd.Timedelta(days=TAIL_DAYS))
        & (p["prod_date"] >= pd.Timestamp(anchor))
    ]
    if tail.empty or tail["q"].mean() <= 0:
        return None
    return float(model_rate(params, anchor, tail["prod_date"]).mean() / tail["q"].mean())


def fit_daily_stream(
    prep: pd.DataFrame,
    stream: str,
    peak: DailyPeak,
    *,
    df_terminal: float,
    config: ForecastConfig | None = None,
    b_prior: float | None = None,
    fit_start: date | None = None,
) -> DailyForecast:
    """Log-residual rate-time fit on producing days, t = 0 at the peak."""
    cfg = config or ForecastConfig()
    p = producing_series(prep, stream)
    start = peak.peak_date if fit_start is None else max(fit_start, peak.peak_date)
    post = p[p["prod_date"] >= pd.Timestamp(start)].copy()
    if len(post) < 10:
        raise ValueError(f"{stream}: {len(post)} producing days from {start} — too few to fit")
    post["t_years"] = _t_years(post["prod_date"], peak.peak_date)
    post["y"] = np.log(post["q"].clip(lower=1e-6))
    windowed = start > peak.peak_date
    lo = (
        None
        if windowed or cfg.qi_anchor_lo_frac is None
        else cfg.qi_anchor_lo_frac * peak.peak_rate
    )
    hi = (
        None
        if windowed or cfg.qi_anchor_hi_frac is None
        else cfg.qi_anchor_hi_frac * peak.peak_rate
    )
    bounds, p0 = _bounds_and_p0(
        MODEL_TYPE,
        peak.peak_rate,
        di_hi=_stream_di_hi(stream, cfg),
        qi_lo=lo,
        qi_hi=hi,
        b_lo=cfg.b_nominal_lo if cfg.b_nominal_lo is not None else B_LO,
        b_hi=cfg.b_nominal_hi if cfg.b_nominal_hi is not None else B_HI,
    )
    rate = _rate_callable(MODEL_TYPE, df_terminal)

    def log_rate(t: NDArray[np.float64], *prm: float) -> NDArray[np.float64]:
        return np.log(np.maximum(rate(t, *prm), 1e-6))

    history_months = int(
        ((post["prod_date"].iloc[-1] - post["prod_date"].iloc[0]).days + 1) / 30.4375
    )
    if fit_start is not None:
        # The prior's taper is about how much decline history exists, not
        # how much the engineer chose to fit.
        history_months = int((prep["prod_date"].max().date() - peak.peak_date).days / 30.4375)
    popt, pred, prior_info = _fit_with_b_prior(
        post,
        target_col="y",
        func=log_rate,
        bounds=bounds,
        p0=p0,
        model_type=MODEL_TYPE,
        config=replace(cfg, b_prior=b_prior),
        n_history_months=history_months,
    )
    qi, di, b = (float(x) for x in popt)
    params = {"qi": qi, "Di": di, "b": b, "Df": float(df_terminal)}
    y = post["y"].to_numpy()
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    _, note = detect_at_bound(
        qi=qi, di=di, b=b, peak_rate=peak.peak_rate, di_hi=_stream_di_hi(stream, cfg)
    )
    diag: dict[str, Any] = {"history_months": history_months}
    if prior_info:
        diag["b_prior"] = prior_info
    return DailyForecast(
        stream=stream,
        method="daily_fit",
        params=params,
        anchor_date=peak.peak_date,
        data_through=prep["prod_date"].max().date(),
        fit_start=fit_start,
        peak_rate=peak.peak_rate,
        fit_r2_log=1.0 - ss_res / ss_tot if ss_tot > 0 else None,
        fit_rmse_log=float(np.sqrt(ss_res / len(y))),
        n_points=len(post),
        at_bound=note,
        tail_ratio=tail_ratio(prep, stream, params, peak.peak_date),
        diagnostics=diag,
    )


def shut_in_forecast(prep: pd.DataFrame, stream: str, *, df_terminal: float) -> DailyForecast:
    """Zero-rate forecast for a shut-in stream (method ``shut_in``)."""
    p = producing_series(prep, stream)
    through = prep["prod_date"].max().date()
    return DailyForecast(
        stream=stream,
        method="shut_in",
        params={"qi": 0.0, "Di": 0.5, "b": 1.0, "Df": float(df_terminal)},
        anchor_date=through,
        data_through=through,
        diagnostics={
            "last_producing_day": p["prod_date"].max().date().isoformat() if len(p) else None,
            "basis": f"no producing day in the trailing {SHUT_IN_DAYS} d — zero unless a restart is set",
        },
    )


def transfer_now(
    prep: pd.DataFrame,
    stream: str,
    *,
    cohort_di: float,
    b: float,
    df_terminal: float,
    donors: dict[str, Any],
) -> DailyForecast:
    """Unpeaked stream: decline from today's producing-day rate with the
    cohort's Di — t = 0 at data_through, no incline credit."""
    q_now = current_rate(prep, stream)
    if q_now is None or q_now <= 0:
        raise ValueError(f"{stream}: no producing days in the trailing {CURRENT_RATE_DAYS} d")
    through = prep["prod_date"].max().date()
    return DailyForecast(
        stream=stream,
        method="transfer_now",
        params={"qi": q_now, "Di": float(cohort_di), "b": float(b), "Df": float(df_terminal)},
        anchor_date=through,
        data_through=through,
        n_points=0,
        diagnostics={
            "donors": donors,
            "qi_basis": f"median producing-day rate, trailing {CURRENT_RATE_DAYS} d",
        },
    )


# ---------------------------------------------------------------------------
# Volumes
# ---------------------------------------------------------------------------


def horizon_end(first_prod: date, years: float) -> date:
    return first_prod + timedelta(days=round(years * DAYS_PER_YEAR_CAL))


def forward_daily_volumes(
    params: dict[str, float], anchor: date, start: date, end: date, uptime: float
) -> pd.Series:
    """Calendar-day volumes (producing-day rate x uptime) for [start, end]."""
    if end < start:
        return pd.Series(dtype=float)
    days = pd.date_range(start, end, freq="D")
    return pd.Series(model_rate(params, anchor, days) * uptime, index=days)


def monthly_volumes(daily: pd.Series) -> pd.Series:
    """Sum daily volumes into calendar months (index = month start)."""
    if daily.empty:
        return daily
    return daily.groupby(daily.index.to_period("M")).sum().rename(lambda p: p.to_timestamp())


def cum_to_date(prep: pd.DataFrame, stream: str) -> float:
    """Reported cumulative (negatives kept: they are the seller's corrections)."""
    return float(prep[VOLUME_COL[stream]].sum(skipna=True))


def remaining_volume(
    fc: DailyForecast, uptime: float, first_prod: date, horizon_years: float
) -> float:
    """Forecast volume from the day after data_through to first_prod + horizon."""
    start = fc.data_through + timedelta(days=1)
    return float(
        forward_daily_volumes(
            fc.params, fc.anchor_date, start, horizon_end(first_prod, horizon_years), uptime
        ).sum()
    )


def forward_effective_decline(fc: DailyForecast) -> float:
    """1-year effective decline of the producing-day rate starting at
    data_through — what the forecast does next (vs ``effective_decline_
    first_year(Di, b)``, which is from the anchor)."""
    t0 = fc.data_through
    q0, q1 = model_rate(fc.params, fc.anchor_date, pd.to_datetime([t0, t0 + timedelta(days=365)]))
    return float(1.0 - q1 / q0) if q0 > 0 else 0.0


def anchor_effective_decline(fc: DailyForecast) -> float:
    return float(effective_decline_first_year(fc.di, fc.b))


# ---------------------------------------------------------------------------
# Review flags
# ---------------------------------------------------------------------------


def review_flags(
    fc: DailyForecast | None,
    classification: str,
    breaks: Sequence[dict[str, Any]],
    *,
    data_through: date,
) -> list[str]:
    flags: list[str] = []
    if classification == "shut_in":
        flags.append("shut_in")
    if classification == "unpeaked":
        flags.append("unpeaked_transfer")
    if fc is not None and fc.at_bound:
        flags.append("at_bound")
    if fc is not None and fc.tail_ratio is not None and abs(fc.tail_ratio - 1.0) > TAIL_TOLERANCE:
        flags.append("tail_mismatch")
    cutoff = data_through - timedelta(days=RECENT_BREAK_DAYS)
    if any(
        date.fromisoformat(b["start"]) >= cutoff
        and not b.get("early_life", False)
        and b.get("material", True)
        for b in breaks
    ):
        flags.append("recent_break")
    return flags
