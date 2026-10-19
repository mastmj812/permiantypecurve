"""Ratio-mode stream fitted on a SEPARATE cohort (engineering_db BOX D107, deal-intake screen curves).

A type curve's ratio-mode stream (ratio_mode.build_ratio_fitted) normally fits ln(stream/oil) = alpha + beta * Np on the
curve's OWN cohort mean series. Water needs a different cohort: vendor-calculated TX water is a frozen filing ratio
(water = k x oil, sql/41), so the WOR must be learned from wells whose water is MEASURED, reaching further than the oil cohort
when it has to. With a ratio cohort the published stream is:

  1. the ratio cohort's own aggregate (same basis + alignment as the curve) -> its mean oil + mean stream series;
  2. alpha / beta fitted on those series, post the ratio cohort's OWN oil peak, beta bounded by its own oil EUR — exactly
     build_ratio_fitted's fit, so a ratio cohort equal to the curve's cohort reproduces build_ratio_fitted;
  3. the forecast = that ratio x the CURVE's fitted oil (so the curve's oil, and any later oil refinement, carries through).

Persisted under ``series["ratio_cohorts"][stream]`` (api10s + n_wells) and stated on the fitted block
(``ratio_cohort: {n_wells, n_requested}``) so save-as-version and reaggregate keep it — the same no-silent-loss posture as
stream_modes_from_series. The stream's bands / percentiles stay the CURVE cohort's (diagnostics, unchanged).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from boxfit.eur import DAYS_PER_YEAR
from boxfit.ratio import (
    fit_ratio_vs_cum_oil,
    implied_effective_decline_yr1,
    ratio_forecast_from_oil_params,
)

_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0
MAX_WELLS = 500


def ratio_cohorts_from_series(series: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """The saved per-stream ratio cohorts (stream -> api10s); {} when the curve has none."""
    out: dict[str, list[str]] = {}
    for s, v in ((series or {}).get("ratio_cohorts") or {}).items():
        ids = (v or {}).get("api10s")
        if ids:
            out[s] = [str(a) for a in ids]
    return out


def validate_ratio_cohorts(
    cohorts: Mapping[str, Sequence[str]] | None, ratio_modes: Mapping[str, str]
) -> dict[str, list[str]]:
    """A ratio cohort is only meaningful on a ratio-mode stream; dedupe, keep order. Raises ValueError (-> 400)."""
    out: dict[str, list[str]] = {}
    for s, ids in (cohorts or {}).items():
        if ratio_modes.get(s) != "ratio":
            raise ValueError(f"ratio_cohorts[{s!r}] needs stream_modes[{s!r}] = 'ratio'")
        seen: dict[str, None] = {}
        for a in ids:
            seen.setdefault(str(a), None)
        if not seen:
            raise ValueError(f"ratio_cohorts[{s!r}] is empty")
        if len(seen) > MAX_WELLS:
            raise ValueError(f"ratio_cohorts[{s!r}] has {len(seen)} wells; max {MAX_WELLS}")
        out[s] = list(seen)
    return out


def build_ratio_fitted_on_cohort(
    *,
    cohort_mean_oil: list[float | None],
    cohort_mean_stream: list[float | None],
    cohort_oil_fitted: Mapping[str, Any],
    tc_oil_fitted: Mapping[str, Any],
    n_months: int,
    n_wells: int,
    n_requested: int,
) -> dict[str, Any] | None:
    """The published fitted block: ratio fitted on the ratio cohort, forecast on the curve's oil. None when the cohort's
    mean series cannot support a ratio (caller -> 422, never a silent Arps fallback)."""
    oil_vols = [(float(v) * _DAYS_PER_MONTH) if v is not None else 0.0 for v in cohort_mean_oil]
    stream_vols = [
        (float(v) * _DAYS_PER_MONTH) if v is not None else 0.0 for v in cohort_mean_stream
    ]
    fit = fit_ratio_vs_cum_oil(
        oil_vols,
        stream_vols,
        start_index=int(cohort_oil_fitted.get("peak_index") or 0),
        np_max_forecast=float(cohort_oil_fitted.get("eur_per_unit") or 0.0),
    )
    if fit is None:
        return None
    series = ratio_forecast_from_oil_params(
        qi=float(tc_oil_fitted["qi"]),
        Di=float(tc_oil_fitted["Di"]),
        b=float(tc_oil_fitted["b"]),
        Df=float(tc_oil_fitted["Df"]),
        qo=tc_oil_fitted.get("qo"),
        peak_index_months=int(tc_oil_fitted.get("peak_index") or 0),
        alpha=fit.alpha,
        beta=fit.beta,
        n_months=n_months,
    )
    return {
        "mode": "ratio",
        "model_type": "ratio",
        "alpha": fit.alpha,
        "beta": fit.beta,
        "sub_mode": fit.sub_mode,
        "r2": fit.r2,
        "n_months": fit.n_months,
        "r_const": fit.r_const,
        "diagnostics": fit.diagnostics,
        "eur_per_unit": series.eur,
        "implied_effective_decline_yr1": implied_effective_decline_yr1(series.rates),
        "oil_ref": {k: tc_oil_fitted.get(k) for k in ("qi", "Di", "b", "Df", "qo", "peak_index")},
        "ratio_cohort": {"n_wells": n_wells, "n_requested": n_requested},
        "smoothed_rate": series.rates,
        "manual_override": False,
    }
