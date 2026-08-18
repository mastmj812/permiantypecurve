"""Ratio-driven forecasting for gas / water — mode of record: ratio vs CUMULATIVE OIL.

Many TX wells' vendor water series is *calculated* (a static WOR x oil
formula, ``wells.water_source = 'calculated'``), and independent water /
gas Arps fits on such streams can be untrustworthy. Ratio mode is the
engineer's alternative: instead of fitting the stream its own decline,
fit the stream's monthly ratio to oil against cumulative oil (Np) and
forecast the stream as ``r(Np) x oil``. Oil is ALWAYS an independent
Arps fit — ratio mode exists only for gas and water, and it is never
auto-selected (flag-only philosophy: the engineer chooses).

Model (decision of record, 2026-08-17 — fitted vs cumulative oil, not
time)::

    ln(r_i) = alpha + beta * Np_i        r_i = vol_stream_i / vol_oil_i

where ``Np_i`` is cumulative oil at the month's MIDPOINT from actuals,
over post-oil-peak months with oil > 0 and stream > 0 (a zero stream
month has no defined ln r; it is excluded from the regression but the
gate below still counts only positive-ratio months). Gate: R^2 >= 0.3
and n >= 6 valid months; on failure the sub-mode falls back to a
CONSTANT ratio = median of the last 6 valid months.

Forecast: derived monthly volume = ``r(Np_mid) x oil monthly volume``
on the SAME 600-month grid ``ramp_arps.trapezoid_eur`` uses, with the
Np trajectory coming from the oil forecast. Np is bounded by oil's
50-yr EUR, and beta is bounded (below), so the exponential can never
explode — and EUR stays the raw 50-yr technical integral (NO economic
cutoff; the horizon is the bound, same as Arps).

Two integration paths exist by design, mirroring the Arps invariant:

  * the stored EUR scalar = sum of midpoint-evaluated monthly volumes
    (``RatioSeries.eur``), and
  * every display / recompute surface = ``trapezoid_eur`` over the
    derived monthly rate grid (``RatioSeries.rates``).

They reconcile to < 0.1% (see ``test_ratio_forecast.py``); keep it
that way.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.ramp_arps import build_ramp_arps_rate

_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0

# ---------------- fit gates + bounds ----------------

# Regression gate (per the design brief): at least this many valid
# (oil > 0, stream > 0) post-oil-peak months and at least this ln-space
# R^2, else fall back to the constant sub-mode.
RATIO_MIN_VALID_MONTHS: int = 6
RATIO_MIN_R2: float = 0.3

# Constant fallback: median ratio of the LAST this-many valid months —
# the most recent behavior is what a static ratio should carry forward.
RATIO_CONSTANT_TAIL_MONTHS: int = 6

# beta bound — dimensionless cap on the TOTAL ln-ratio drift the fitted
# exponential may express over the full 50-yr forecast:
#
#     |beta| <= RATIO_MAX_ABS_LN_DRIFT / Np_max_forecast
#
# where Np_max_forecast is the oil forecast's 50-yr Np (its EUR, in the
# same normalization units the fit ran in — absolute BBL per-well,
# BBL-per-1,000-ft on a type curve; a fixed per-BBL constant can't
# serve both bases, which is why the band is derived from the data).
#
# Derivation of the 3.0: e^3 ~ 20x. Permian solution-GOR climbs ~2-5x
# over a well's life once flowing pressure drops below bubble point;
# WOR drift on measured Permian water rarely exceeds ~10x lifecycle.
# 20x covers both with generous headroom while keeping the tail ratio
# finite: because the forecast's Np is bounded by oil's EUR, the
# evaluated ratio can never exceed e^alpha * e^3 — an exploding
# exponential is structurally impossible. A steeper fitted slope than
# this band means the log-linear model is extrapolating noise, not
# physics; the slope is clipped to the band and alpha re-fit.
RATIO_MAX_ABS_LN_DRIFT: float = 3.0


@dataclass(frozen=True)
class RatioFit:
    """Fitted ratio-vs-cum-oil parameters for one derived stream.

    ``sub_mode`` is ``"exp_cum"`` (ln r = alpha + beta * Np passed the
    gate) or ``"constant"`` (fallback: beta = 0, alpha = ln(median of
    the last valid months' ratios), ``r_const`` carries the median).
    ``r2`` / ``n_months`` describe the regression (n_months = valid
    positive-ratio months in the fit window, recorded for both
    sub-modes). ``diagnostics`` carries the audit trail (np window,
    clipping, the rejected regression when the gate failed).
    """

    alpha: float
    beta: float
    sub_mode: str  # "exp_cum" | "constant"
    r2: float | None
    n_months: int
    r_const: float | None
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class RatioSeries:
    """Derived stream evaluated on the monthly grid.

    ``rates`` — pointwise derived rate (r(Np at month start) x oil
    rate), the chart / trapezoid-EUR surface. ``eur`` — the canonical
    stored scalar: sum of r(Np_mid) x oil monthly volume. ``np_total``
    — the oil Np the grid reached (bounded by oil's EUR by
    construction).
    """

    rates: list[float]
    eur: float
    np_total: float


def _lstsq_line(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Least-squares (intercept, slope) for y = a + b*x."""
    n = len(x)
    xm = float(np.mean(x))
    ym = float(np.mean(y))
    sxx = float(np.sum((x - xm) ** 2))
    if n < 2 or sxx <= 0.0:
        return ym, 0.0
    sxy = float(np.sum((x - xm) * (y - ym)))
    slope = sxy / sxx
    return ym - slope * xm, slope


def _line_r2(x: np.ndarray, y: np.ndarray, alpha: float, beta: float) -> float:
    """R^2 of y ~ alpha + beta*x. A zero-variance target that the line
    reproduces (constant ratio fitted exactly) scores 1.0 — same
    convention as ``fit._r_squared``. "Zero variance" is judged against
    a float-noise floor relative to the target's magnitude: an exactly
    constant WOR computed as stream/oil carries ~1e-16-scale rounding
    noise that is uncorrelated with Np, and a literal ss_tot > 0 test
    would score that perfect ratio ~0 and bounce it to the constant
    fallback."""
    pred = alpha + beta * x
    ss_res = float(np.sum((y - pred) ** 2))
    ym = float(np.mean(y))
    ss_tot = float(np.sum((y - ym) ** 2))
    noise_floor = 1e-18 * len(y) * max(1.0, ym * ym)
    if ss_tot <= noise_floor:
        return 1.0 if ss_res <= noise_floor else 0.0
    return 1.0 - ss_res / ss_tot


def fit_ratio_vs_cum_oil(
    oil_volumes: Sequence[float],
    stream_volumes: Sequence[float],
    *,
    start_index: int,
    np_max_forecast: float,
) -> RatioFit | None:
    """Fit ``ln(stream/oil) = alpha + beta * Np`` on post-oil-peak months.

    ``oil_volumes`` / ``stream_volumes`` are the FULL-history monthly
    volumes (actuals for a per-well fit; mean-series rate x days for a
    type-curve cohort — "aggregate stream / aggregate cum oil"), in
    matching normalization units. ``start_index`` is the oil peak index
    — the fit window runs from there forward, but Np accumulates from
    month 0 so it is genuinely cumulative oil. ``np_max_forecast`` is
    the oil forecast's 50-yr Np (EUR) in the same units; it sets the
    data-derived beta band (see ``RATIO_MAX_ABS_LN_DRIFT``).

    Returns None when no valid (oil > 0, stream > 0) post-peak month
    exists at all — there is no ratio to carry forward.
    """
    oil = np.asarray([float(v) if v is not None and math.isfinite(float(v)) else 0.0
                      for v in oil_volumes], dtype=float)
    stm = np.asarray([float(v) if v is not None and math.isfinite(float(v)) else 0.0
                      for v in stream_volumes], dtype=float)
    n = min(len(oil), len(stm))
    if n == 0 or start_index >= n:
        return None
    oil = oil[:n]
    stm = stm[:n]

    # Np at each month's midpoint, from actuals over the whole history.
    cum_before = np.concatenate(([0.0], np.cumsum(oil)[:-1]))
    np_mid = cum_before + oil / 2.0

    idx = np.arange(n)
    # Valid regression months: post-oil-peak, oil > 0, stream > 0
    # (stream >= 0 months are admitted to the window per the design but
    # a zero stream has no finite ln r — excluded from the regression).
    valid = (idx >= int(start_index)) & (oil > 0.0) & (stm > 0.0)
    n_valid = int(np.count_nonzero(valid))
    if n_valid == 0:
        return None

    r = stm[valid] / oil[valid]
    x = np_mid[valid]
    y = np.log(r)
    np_window = float(np.max(x) - np.min(x)) if n_valid > 1 else 0.0

    alpha, beta = _lstsq_line(x, y)
    beta_unclipped = beta
    beta_clipped = False
    beta_cap = (
        RATIO_MAX_ABS_LN_DRIFT / float(np_max_forecast) if np_max_forecast > 0 else 0.0
    )
    if beta_cap > 0 and abs(beta) > beta_cap:
        # Clip to the band, then re-fit alpha with the slope pinned
        # (least squares in alpha alone: mean residual).
        beta = math.copysign(beta_cap, beta)
        alpha = float(np.mean(y - beta * x))
        beta_clipped = True
    r2 = _line_r2(x, y, alpha, beta)

    diagnostics: dict[str, Any] = {
        "np_window": np_window,
        "np_max_forecast": float(np_max_forecast),
        "beta_cap": beta_cap,
        "beta_unclipped": beta_unclipped,
        "beta_clipped": beta_clipped,
        "n_valid_months": n_valid,
    }

    if n_valid >= RATIO_MIN_VALID_MONTHS and r2 >= RATIO_MIN_R2:
        return RatioFit(
            alpha=float(alpha),
            beta=float(beta),
            sub_mode="exp_cum",
            r2=float(r2),
            n_months=n_valid,
            r_const=None,
            diagnostics=diagnostics,
        )

    # Constant fallback: median ratio of the last valid months. beta=0,
    # alpha=ln(median) so the evaluators need no special casing.
    tail = r[-RATIO_CONSTANT_TAIL_MONTHS:]
    r_const = float(np.median(tail))
    diagnostics["rejected_fit"] = {
        "alpha": float(alpha),
        "beta": float(beta),
        "r2": float(r2),
    }
    return RatioFit(
        alpha=float(math.log(r_const)) if r_const > 0 else 0.0,
        beta=0.0,
        sub_mode="constant",
        r2=float(r2),
        n_months=n_valid,
        r_const=r_const,
        diagnostics=diagnostics,
    )


def derive_ratio_stream(
    oil_rates: Sequence[float],
    *,
    alpha: float,
    beta: float,
) -> RatioSeries:
    """Evaluate the derived stream on the oil forecast's monthly grid.

    Month i's oil volume uses the exact ``trapezoid_eur`` segment rule
    (average of its endpoint rates x days; final month flat-extrapolated)
    so the oil Np trajectory here integrates to the same number every
    display surface shows for oil. The stored EUR is the sum of
    ``r(Np_mid_i) x oil_vol_i``; the rate grid is the pointwise product
    ``r(Np at month start) x oil_rate`` — trapezoid-integrating it
    reconciles with the stored scalar to < 0.1% (the two-paths
    invariant, tested).
    """
    q = np.asarray([float(v) for v in oil_rates], dtype=float)
    n = len(q)
    if n == 0:
        return RatioSeries(rates=[], eur=0.0, np_total=0.0)
    q = np.maximum(q, 0.0)
    q_next = np.concatenate((q[1:], q[-1:]))  # final month flat
    vol = (q + q_next) / 2.0 * _DAYS_PER_MONTH
    np_start = np.concatenate(([0.0], np.cumsum(vol)[:-1]))
    np_mid = np_start + vol / 2.0

    ratio_mid = np.exp(alpha + beta * np_mid)
    ratio_pt = np.exp(alpha + beta * np_start)
    eur = float(np.sum(ratio_mid * vol))
    rates = [float(v) for v in ratio_pt * q]
    return RatioSeries(rates=rates, eur=eur, np_total=float(np_start[-1] + vol[-1]))


def derive_ratio_rates_masked(
    oil_rates: Sequence[float | None],
    *,
    alpha: float,
    beta: float,
) -> list[float | None]:
    """None-aware variant for TC panel rows (peak_ramp front-padding).

    None oil months contribute no Np and yield None derived rates, so a
    ratio-mode well slots into the aggregation panel exactly where its
    oil forecast does.
    """
    dense = [0.0 if v is None or not math.isfinite(float(v)) else float(v) for v in oil_rates]
    derived = derive_ratio_stream(dense, alpha=alpha, beta=beta).rates
    return [None if oil_rates[i] is None else derived[i] for i in range(len(oil_rates))]


def ratio_forecast_from_oil_params(
    *,
    qi: float,
    Di: float,
    b: float,
    Df: float,
    qo: float | None = None,
    peak_index_months: int | None = None,
    alpha: float,
    beta: float,
    n_months: int = 600,
) -> RatioSeries:
    """Derived stream from an oil ramp+Arps parameter set.

    THE evaluation path for both the stored EUR scalar and every
    recompute-from-params surface — the oil grid is the same 600-month
    ``build_ramp_arps_rate`` grid the display EUR uses, so persist-time
    and display-time numbers can't drift.
    """
    oil_rates = build_ramp_arps_rate(
        n_months=n_months,
        qo=qo if qo is not None else qi,
        qi=qi,
        peak_index=int(peak_index_months or 0),
        Di=Di,
        b=b,
        Df=Df,
    )
    return derive_ratio_stream(oil_rates, alpha=alpha, beta=beta)


def implied_effective_decline_yr1(rates: Sequence[float]) -> float | None:
    """Effective year-1 decline of the DERIVED series (0-1 fraction).

    A ratio stream has no Di; the engineer still thinks in effective
    decline, so report 1 - q(peak + 12 mo) / q(peak) measured on the
    derived monthly grid (peak searched in the first 24 months). None
    when the series is too short or peaks at zero.
    """
    if len(rates) < 13:
        return None
    arr = np.asarray(rates[: min(len(rates), 24)], dtype=float)
    peak_idx = int(np.argmax(arr))
    peak_rate = float(arr[peak_idx])
    if peak_rate <= 0 or peak_idx + 12 >= len(rates):
        return None
    return 1.0 - float(rates[peak_idx + 12]) / peak_rate


def is_ratio_params(params: dict[str, Any] | None) -> bool:
    """True when a forecast params JSONB is a ratio-mode payload."""
    return params is not None and params.get("mode") == "ratio"
