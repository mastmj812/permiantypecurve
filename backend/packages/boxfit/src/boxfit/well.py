"""Fit all three streams for ONE well from its monthly frame — no DB.

The per-well pipeline of record, split out of anduin's
``app.forecasting.orchestrator.forecast_well`` so the BOX batch
(engineering_db) fits wells exactly as anduin does. anduin's
``forecast_well`` is now a thin wrapper: it looks up the well's sub-basin /
bench, loads ``production_monthly``, calls :func:`fit_well_streams`, and
persists / prunes through the callbacks.

Key domain rule: EVERY stream anchors on its OWN detected peak. Water peaks
hard in month 0-1 (flowback), months before oil ramps up; gas commonly
peaks AFTER oil as the GOR climbs (39% of forecasted wells in this
dataset, p90 +4 months, with the true gas peak up to 1.5x the gas rate
at the oil month) — inheriting the oil peak under-read each stream's
qi and started the fit on the wrong limb. A stream with no real
production (no detectable peak / zero-rate peak) is SKIPPED — no
forecast — rather than fit against zeros anchored on the oil month,
which under peak-anchored qi bounds manufactured phantom oil-scale
forecasts. Each stream's per-well forecast is expressed in
years-since-first-prod (the peak is an internal ramp anchor), so
per-stream peaks stay coherent — all three streams still map back to
one first-prod calendar. See ``detect_stream_peaks``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace

import pandas as pd
from structlog import get_logger

from boxfit.b_prior import lookup_b_prior
from boxfit.fit import (
    STREAM_DOWNTIME_FLOOR_FIELD,
    STREAM_ECON_LIMIT_FIELD,
    fit_rate_cum,
    fit_rate_time,
    fit_with_fallback,
)
from boxfit.peak_detection import PeakResult, detect_onset, detect_peak
from boxfit.ramp_arps import compute_total_eur
from boxfit.types import ForecastConfig, ForecastResult

log = get_logger("forecasting.orchestrator")

STREAMS: tuple[str, ...] = ("oil", "gas", "water")

# Rate column each stream's peak is detected on — every stream anchors
# on its OWN peak (gas previously inherited oil's; see module docstring
# for why that under-read gas on rising-GOR wells).
_PEAK_RATE_COLUMN: dict[str, str] = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_mcfd",
    "water": "rate_calday_bwpd",
}

_STREAM_RATE_COL: dict[str, str] = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_mcfd",
    "water": "rate_calday_bwpd",
}

# Columns of the monthly frame the fitter consumes (production_monthly
# shape). Volumes are what the cum-fit integrates; rates are calendar-day.
MONTHLY_COLUMNS: tuple[str, ...] = (
    "prod_date",
    "oil_bbl",
    "gas_mcf",
    "water_bbl",
    "producing_days",
    "rate_calday_bopd",
    "rate_calday_mcfd",
    "rate_calday_bwpd",
)
_NUMERIC_COLUMNS: list[str] = list(MONTHLY_COLUMNS[1:])


def prepare_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """Coalesce NULL monthly volumes / rates to 0 (in place; returns ``df``).

    Raw warehouse production carries reporting-gap months — a real calendar
    row where a stream's volume + rate are NULL (operator filed a partial
    report; common on some NM wells). A single such gap is catastrophic for
    the cum-fit: `_post_peak_slice` does `vol_col.cumsum()`, NaN poisons the
    cumulative array from the gap forward, and scipy's curve_fit raises
    "array must not contain infs or NaNs". The orchestrator's broad except
    swallows that as a fit failure, so the well silently gets NO forecast
    row for that stream — surfacing later as a "missing" well in the TC
    workspace that the Forecast button can't repair (it re-runs this same
    path and fails identically). Treat a gap as zero reported production:
    the cum stays calendar-aligned (the gap contributes nothing), the
    zero-rate month is then dropped by the downtime filter, and the fit
    proceeds. Wells with no gaps are unaffected (no-op fill).
    """
    df[_NUMERIC_COLUMNS] = df[_NUMERIC_COLUMNS].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return df


def df_terminal_for_subbasin(subbasin: str | None, config: ForecastConfig) -> float:
    """Terminal Df for the well's Permian sub-basin. Midland uses the
    shallower ``df_terminal_midland`` (its boundary-dominated tails flatten
    more); Delaware and every other sub-basin use ``df_terminal_per_year``
    (the policy: Midland 0.06, else 0.08)."""
    if subbasin and subbasin.strip().lower() == "midland":
        return config.df_terminal_midland
    return config.df_terminal_per_year


def df_terminal_for_cohort(subbasins: Iterable[str | None], config: ForecastConfig) -> float:
    """Terminal Df for a type-curve cohort's aggregate P50 fit.

    The cohort's P50 is a single aggregate series, so one Df must be
    imposed on the fit. Policy (of record): use Midland's shallower
    ``df_terminal_midland`` when Midland wells are the MAJORITY (> 50%) of
    the cohort, else the Delaware/default ``df_terminal_per_year`` — the
    per-well ``df_terminal_for_subbasin`` rule (Midland 0.06, else 0.08)
    lifted to the cohort grain. Empty cohort → the default.

    NULL / blank subbasins count toward the total but never as Midland,
    so a cohort that is only a plurality Midland (not a majority) keeps
    the default 0.08 — deliberately conservative about shallowing the
    tail (which raises EUR).
    """
    total = 0
    midland = 0
    for sb in subbasins:
        total += 1
        if sb and sb.strip().lower() == "midland":
            midland += 1
    if total > 0 and midland / total > 0.5:
        return config.df_terminal_midland
    return config.df_terminal_per_year


def detect_stream_peaks(
    monthly: pd.DataFrame,
) -> dict[str, PeakResult | None]:
    """Per-stream peak month for one well — each stream on its OWN rate.

    Water peaks hard in month 0-1 (flowback) months before oil; gas
    commonly peaks AFTER oil as the GOR climbs (39% of forecasted
    wells, p90 +4 months). Anchoring either on the oil peak reads the
    wrong qi and starts the fit slice on the wrong limb.

    A zero-rate "peak" (all-zero column — zeros aren't null, so
    detect_peak still returns index 0) isn't a real peak: the stream
    comes back None and the orchestrator SKIPS it. The old behavior —
    falling back to the oil anchor — fit zeros against oil-scale
    peak-anchored qi bounds and manufactured phantom forecasts.

    Pure function (takes a frame, no DB) so the per-stream rule is unit
    testable the same way ``cohort.classify_history`` is.
    """

    def _real(p: PeakResult | None) -> PeakResult | None:
        return p if (p is not None and p.peak_rate > 0) else None

    return {
        stream: _real(detect_peak(monthly, rate_column=col))
        for stream, col in _PEAK_RATE_COLUMN.items()
    }


def fit_well_streams(
    monthly: pd.DataFrame,
    *,
    config: ForecastConfig | None = None,
    subbasin: str | None = None,
    formation_blueox: str | None = None,
    api10: str | None = None,
    on_result: Callable[[str, ForecastResult], None] | None = None,
    on_no_peak: Callable[[str], None] | None = None,
) -> dict[str, ForecastResult | None]:
    """Fit oil / gas / water for one well's monthly frame.

    ``monthly`` is the production_monthly-shaped frame (see
    ``MONTHLY_COLUMNS``), already passed through :func:`prepare_monthly`.
    ``subbasin`` picks the terminal Df (Midland 0.06, else 0.08);
    ``subbasin`` + ``formation_blueox`` key the short-history b prior.
    ``api10`` is log context only.

    Every stream anchors on its OWN detected peak (see
    ``detect_stream_peaks``). Each stream's ramp prefix is anchored on its
    own ONSET (first producing month) rather than the well's first-prod,
    so leading zero / sub-floor months don't inflate the ramp length or
    the type-curve timing (``onset_index_months`` records the offset; see
    ``detect_onset``). Returns {stream: ForecastResult or None on fit
    failure / no peak}.

    Callbacks fire in stream order, interleaved exactly as the fits run:
    ``on_result(stream, result)`` after each successful fit (anduin
    persists here), ``on_no_peak(stream)`` for a stream with no real
    production (anduin prunes its stale machine fit here). Neither fires
    on the empty-frame / no-peaks-at-all early returns.
    """
    cfg = config or ForecastConfig()
    # Basin-aware terminal Df: Midland wells get the shallower tail. Bake
    # the chosen Df into the config used for every stream's fit + EUR.
    cfg = replace(cfg, df_terminal_per_year=df_terminal_for_subbasin(subbasin, cfg))

    out: dict[str, ForecastResult | None] = {"oil": None, "gas": None, "water": None}
    if monthly.empty:
        log.warning("no_production", api10=api10)
        return out

    # Per-stream peak: every stream anchors on its own (gas commonly
    # peaks after oil; water before — see detect_stream_peaks).
    peaks = detect_stream_peaks(monthly)
    if all(p is None for p in peaks.values()):
        log.warning("no_stream_peaks", api10=api10)
        return out

    # Default "rate_cum" runs the wrapper that retries with rate-time
    # when Di pins at a bound (see fit_with_fallback). Explicit "rate_time" or
    # "rate_cum_strict" opt out — useful for tests and per-well overrides.
    if cfg.fit_method == "rate_time":
        fit_fn = fit_rate_time
    elif cfg.fit_method == "rate_cum_strict":
        fit_fn = fit_rate_cum
    else:
        fit_fn = fit_with_fallback

    for stream in STREAMS:
        # Each stream is fit and ramp-anchored against ITS OWN peak;
        # peak_index_months (the ramp length) is per-stream. A stream
        # with no real production has no peak — skip it (no forecast
        # row) rather than fit zeros against another stream's anchor.
        peak = peaks[stream]
        if peak is None:
            log.info("no_stream_production_skip", api10=api10, stream=stream)
            # Data can't support this stream anymore — the caller prunes
            # any leftover unlocked machine fit so it doesn't keep feeding
            # the TC band. Deliberately NOT signalled in the all-streams-None
            # / empty-frame branches above: a well with no usable production
            # at all is more likely a sync hiccup than a per-stream
            # restatement, and bulk-deleting every stream on a bad sync
            # would be worse than a stale row.
            if on_no_peak is not None:
                on_no_peak(stream)
            continue
        peak_index_abs = int(peak.peak_index)
        # Short-history b regularization: bench prior for THIS stream, on
        # the default path only (explicit rate_time / rate_cum_strict opt
        # out). A caller-supplied cfg.b_prior wins. The fitter decides
        # whether the prior carries any weight (none at >= 36 fit months).
        stream_cfg, prior = cfg, None
        if fit_fn is fit_with_fallback and cfg.b_prior_enabled and cfg.b_prior is None:
            prior = lookup_b_prior(subbasin, formation_blueox, stream)
            stream_cfg = replace(cfg, b_prior=prior.b)
        try:
            result = fit_fn(
                monthly,
                model_type=cfg.model_type,
                peak=peak,
                stream=stream,
                config=stream_cfg,
            )
        except Exception as e:
            log.exception("fit_failed", api10=api10, stream=stream, err=str(e))
            continue

        # Onset: trim leading sub-floor months so the ramp anchors at the
        # stream's first PRODUCING month, not the well's first-prod. Without
        # this, a delayed-onset / leading-zero stream (water flowback that
        # starts months late, or simply unreported early months) inflates
        # peak_index_months by those phantom months and pushes the
        # type-curve peak timing later. onset is <= peak by construction.
        floor = getattr(cfg, STREAM_DOWNTIME_FLOOR_FIELD[stream])
        onset_index = min(
            detect_onset(monthly, rate_column=_STREAM_RATE_COL[stream], floor=floor),
            peak_index_abs,
        )
        # Ramp length and qo are now ONSET-relative: ramp from the onset
        # rate up to qi over (peak - onset) months. onset_index_months
        # records the offset from well first-prod so the chart / TC can
        # place the curve on the right month.
        peak_index_months = peak_index_abs - onset_index
        qo = _stream_rate_at_index(monthly, onset_index, stream)

        # Stamp the ramp params. params dict also carries them so the
        # evaluator can pick them up without the row-level fields.
        # EUR is recomputed as ramp_eur + arps_eur to match the
        # ramp+Arps model — _build_result's Arps-only EUR was right
        # for the fit math but doesn't reflect the full forecast.
        new_params = dict(result.params)
        if qo is not None:
            new_params["qo"] = qo
        if peak_index_months > 0:
            new_params["peak_index_months"] = peak_index_months
        if onset_index > 0:
            new_params["onset_index_months"] = onset_index
        total_eur = compute_total_eur(
            model_type=result.model_type,
            params=new_params,
            horizon_years=cfg.horizon_years,
            economic_limit=getattr(cfg, STREAM_ECON_LIMIT_FIELD[stream]),
        )
        diagnostics = result.diagnostics
        if diagnostics and "b_prior" in diagnostics and prior is not None:
            # Record WHERE the prior came from so the row is auditable.
            diagnostics = {
                **diagnostics,
                "b_prior": {
                    **diagnostics["b_prior"],
                    "source": prior.source,
                    "key": prior.key,
                    "n_wells": prior.n_wells,
                },
            }
        result = replace(
            result,
            params=new_params,
            qo=qo,
            peak_index_months=peak_index_months if peak_index_months > 0 else None,
            eur=total_eur,
            diagnostics=diagnostics,
        )

        out[stream] = result
        if on_result is not None:
            on_result(stream, result)

    return out


def stream_rate_at_peak(monthly: pd.DataFrame, peak: PeakResult, stream: str) -> float:
    """Return the stream's calday rate at ``peak``'s month.

    Public helper — the cohort-transfer endpoint anchors short-history
    wells' qi with this same value, so the semantic needs to match the
    per-stream qi the autoforecast picks. Pass the peak the stream is
    anchored on (oil peak for oil/gas, water peak for water). 0.0 when
    the row is missing or the rate is null.
    """
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if peak.peak_index >= len(df):
        return 0.0
    val = df.iloc[peak.peak_index][_STREAM_RATE_COL[stream]]
    return float(val) if val is not None and not pd.isna(val) else 0.0


def _stream_rate_at_index(monthly: pd.DataFrame, index: int, stream: str) -> float | None:
    """Return the stream's calday rate at ``index`` (0-based, chronological).

    Anchors ``qo`` at the stream's onset month rather than the well's
    first-prod month. ``None`` when out of range or null."""
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if index < 0 or index >= len(df):
        return None
    val = df.iloc[index][_STREAM_RATE_COL[stream]]
    return float(val) if val is not None and not pd.isna(val) else None


def stream_rate_at_first_prod(monthly: pd.DataFrame, stream: str) -> float | None:
    """Return the stream's calday rate at the well's first-prod month.

    Anchors the ramp prefix's ``qo`` value — the rate at the start of
    the well's life, before the ramp-up to peak. ``None`` when the
    first row is missing or the rate is null, which the evaluator
    treats as "no ramp data available; fall back to pure Arps." See
    boxfit.ramp_arps.evaluate_well_rate.
    """
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if df.empty:
        return None
    val = df.iloc[0][_STREAM_RATE_COL[stream]]
    if val is None or pd.isna(val):
        return None
    return float(val)
