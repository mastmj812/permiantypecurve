"""Type-curve ratio-mode streams (gas/water forecast as ratio x TC oil).

A TC's gas/water stream may be BUILT ratio-mode: the ratio is fitted on
the cohort's aggregated per-1,000-ft MEAN series (aggregate stream /
aggregate cum oil), and the published stream forecast is that ratio
applied to the TC's fitted oil stream. Aggregation math and n_wells
semantics are untouched — only the published ``fitted`` block for the
stream changes shape (mode/alpha/beta/... instead of qi/Di/b/Df).

Mode is an explicit per-request input (never inferred from data), and
persistence follows the no-silent-conversion posture: version-save and
reaggregation preserve a stream's ratio mode by reading it back from
the stored series (``stream_modes_from_series``); an Arps TweakPanel
override on a ratio-mode stream is refused rather than silently
converting it (``ratio_override_conflicts``).
"""

from __future__ import annotations

from typing import Any

from app.forecasting.eur import DAYS_PER_YEAR
from app.forecasting.ratio import (
    fit_ratio_vs_cum_oil,
    implied_effective_decline_yr1,
    ratio_forecast_from_oil_params,
)

_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0

RATIO_ELIGIBLE_STREAMS: tuple[str, ...] = ("gas", "water")
STREAM_MODES: tuple[str, ...] = ("arps", "ratio")


def validate_stream_modes(modes: dict[str, str] | None) -> dict[str, str]:
    """Validate a per-stream mode map. Keys must be gas/water (oil is
    always independent Arps), values 'arps' | 'ratio'. Raises ValueError
    with a user-facing message on violation. Returns only the ratio
    entries (arps is the default; carrying it is a no-op)."""
    if not modes:
        return {}
    out: dict[str, str] = {}
    for stream, mode in modes.items():
        if stream not in RATIO_ELIGIBLE_STREAMS:
            raise ValueError(
                f"stream_modes key {stream!r} invalid — oil is always an "
                "independent Arps fit; ratio mode is gas/water only"
            )
        if mode not in STREAM_MODES:
            raise ValueError(f"stream_modes[{stream!r}] must be 'arps' or 'ratio', got {mode!r}")
        if mode == "ratio":
            out[stream] = mode
    return out


def build_ratio_fitted(
    *,
    mean_oil: list[float | None],
    mean_stream: list[float | None],
    oil_fitted: dict[str, Any],
    n_months: int,
) -> dict[str, Any] | None:
    """Build the published ``fitted`` block for a ratio-mode TC stream.

    Fit: ratio of the cohort's aggregated MEAN series (stream / oil),
    vs cumulative MEAN oil at month midpoints, post-oil-peak (the oil
    fit's ``peak_index`` anchors the window). Forecast: ratio x the TC
    oil fitted stream on the same ``n_months`` monthly grid the Arps
    ``smoothed_rate`` uses. ``eur_per_unit`` is the derived midpoint-
    volume sum (same units as the oil fit's, e.g. per 1,000 ft).

    Returns None when the mean series can't support a ratio (no valid
    post-peak months) — the caller surfaces that as a 422, never a
    silent Arps fallback.
    """
    oil_vols = [
        (float(v) * _DAYS_PER_MONTH) if v is not None else 0.0 for v in mean_oil
    ]
    stream_vols = [
        (float(v) * _DAYS_PER_MONTH) if v is not None else 0.0 for v in mean_stream
    ]
    np_max = float(oil_fitted.get("eur_per_unit") or 0.0)
    fit = fit_ratio_vs_cum_oil(
        oil_vols,
        stream_vols,
        start_index=int(oil_fitted.get("peak_index") or 0),
        np_max_forecast=np_max,
    )
    if fit is None:
        return None

    series = ratio_forecast_from_oil_params(
        qi=float(oil_fitted["qi"]),
        Di=float(oil_fitted["Di"]),
        b=float(oil_fitted["b"]),
        Df=float(oil_fitted["Df"]),
        qo=oil_fitted.get("qo"),
        peak_index_months=int(oil_fitted.get("peak_index") or 0),
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
        # The oil fit the ratio rides on, snapshotted for the audit
        # trail (the live forecast always re-derives from the CURRENT
        # oil fitted block at evaluation surfaces that recompute).
        "oil_ref": {
            k: oil_fitted.get(k) for k in ("qi", "Di", "b", "Df", "qo", "peak_index")
        },
        "smoothed_rate": series.rates,
        "manual_override": False,
    }


def stream_modes_from_series(series: dict[str, Any] | None) -> dict[str, str]:
    """Recover the per-stream modes a saved series was built under.

    Used by reaggregate / version-save so a ratio-mode stream is
    PRESERVED when the client doesn't (re)state modes — a recompute
    must never silently convert a ratio stream back to Arps.
    """
    streams = (series or {}).get("streams") or {}
    return {
        s: "ratio"
        for s in RATIO_ELIGIBLE_STREAMS
        if ((streams.get(s) or {}).get("fitted") or {}).get("mode") == "ratio"
    }


def ratio_override_conflicts(
    series: dict[str, Any] | None,
    fit_overrides: dict[str, Any] | None,
) -> list[str]:
    """Streams where an Arps fit-override targets a ratio-mode stream.

    Applying qi/Di/b/Df to a ratio-mode stream would silently convert
    it to Arps — the caller must refuse (400) instead. Empty list =
    no conflict."""
    if not fit_overrides:
        return []
    ratio_streams = stream_modes_from_series(series)
    return sorted(s for s in fit_overrides if s in ratio_streams)
