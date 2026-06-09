"""Run forecasts for one or many wells across oil/gas/water streams.

Glue between the math (`fit.py`) and the DB (`forecasts` table). Key
domain rule: oil and gas share oil's peak month (gas inherits it), while
WATER is anchored on its own peak — Permian flowback water peaks hard in
month 0-1 and declines immediately, months before oil ramps to peak, so
inheriting the oil peak under-reads water qi and the early decline. Each
stream's per-well forecast is expressed in years-since-first-prod (the
peak is an internal ramp anchor), so per-stream peaks stay coherent —
all three streams still map back to one first-prod calendar. See
``detect_stream_peaks``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Forecast, ProductionMonthly, Stream, Well
from app.forecasting.fit import (
    STREAM_DOWNTIME_FLOOR_FIELD,
    STREAM_ECON_LIMIT_FIELD,
    fit_rate_cum,
    fit_rate_time,
    fit_with_fallback,
)
from app.forecasting.peak_detection import (
    PeakResult,
    detect_oil_peak,
    detect_onset,
    detect_peak,
)
from app.forecasting.ramp_arps import compute_total_eur
from app.forecasting.types import ForecastConfig, ForecastResult

log = get_logger("forecasting.orchestrator")

STREAMS: tuple[str, ...] = ("oil", "gas", "water")

# Stream whose peak each stream anchors on. Oil and gas share oil's peak
# (gas tracks oil closely enough that one t=0 stays coherent); water gets
# its own — Permian flowback water peaks hard in month 0-1 and declines
# immediately, months before oil ramps to peak. See detect_stream_peaks.
_PEAK_RATE_COLUMN: dict[str, str] = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_bopd",  # inherits oil's peak month
    "water": "rate_calday_bwpd",
}


def df_terminal_for_subbasin(subbasin: str | None, config: ForecastConfig) -> float:
    """Terminal Df for the well's Permian sub-basin. Midland uses the
    shallower ``df_terminal_midland`` (its boundary-dominated tails flatten
    more); Delaware and every other sub-basin use ``df_terminal_per_year``
    (the policy: Midland 0.06, else 0.08)."""
    if subbasin and subbasin.strip().lower() == "midland":
        return config.df_terminal_midland
    return config.df_terminal_per_year


def detect_stream_peaks(
    monthly: pd.DataFrame,
) -> dict[str, PeakResult | None]:
    """Per-stream peak month for one well.

    Oil and gas share the oil peak (gas inherits it by convention).
    Water is detected independently on its own rate column: anchoring
    water on the oil peak reads a low qi (the hard early water peak has
    already decayed by the oil peak) and a shallow Di (the steep early
    water decline sits upstream of the post-peak fit slice). When a well
    has no detectable water peak (no water production / all-null), water
    falls back to the oil peak so it still gets a forecast anchored
    somewhere sensible rather than being dropped.

    Pure function (takes a frame, no DB) so the per-stream rule is unit
    testable the same way ``cohort.classify_history`` is.
    """
    oil_peak = detect_oil_peak(monthly)
    water_peak = detect_peak(monthly, rate_column=_PEAK_RATE_COLUMN["water"])
    # A zero-rate "peak" (all-zero water column — zeros aren't null, so
    # detect_peak still returns index 0) isn't a real peak. Treat it as
    # "no water" and fall back to the oil anchor.
    has_water = water_peak is not None and water_peak.peak_rate > 0
    return {
        "oil": oil_peak,
        "gas": oil_peak,
        "water": water_peak if has_water else oil_peak,
    }


def _load_monthly(session: Session, api10: str) -> pd.DataFrame:
    rows = session.execute(
        select(
            ProductionMonthly.prod_date,
            ProductionMonthly.oil_bbl,
            ProductionMonthly.gas_mcf,
            ProductionMonthly.water_bbl,
            ProductionMonthly.producing_days,
            ProductionMonthly.rate_calday_bopd,
            ProductionMonthly.rate_calday_mcfd,
            ProductionMonthly.rate_calday_bwpd,
        )
        .where(ProductionMonthly.api10 == api10)
        .order_by(ProductionMonthly.prod_date)
    ).all()
    df = pd.DataFrame(rows, columns=[
        "prod_date", "oil_bbl", "gas_mcf", "water_bbl", "producing_days",
        "rate_calday_bopd", "rate_calday_mcfd", "rate_calday_bwpd",
    ])
    # Coalesce NULL monthly volumes / rates to 0. Raw warehouse production
    # carries reporting-gap months — a real calendar row where a stream's
    # volume + rate are NULL (operator filed a partial report; common on
    # some NM wells). A single such gap is catastrophic for the cum-fit:
    # `_post_peak_slice` does `vol_col.cumsum()`, NaN poisons the cumulative
    # array from the gap forward, and scipy's curve_fit raises
    # "array must not contain infs or NaNs". The orchestrator's broad
    # except swallows that as a fit failure, so the well silently gets NO
    # forecast row for that stream — surfacing later as a "missing" well in
    # the TC workspace that the Forecast button can't repair (it re-runs
    # this same path and fails identically). Treat a gap as zero reported
    # production: the cum stays calendar-aligned (the gap contributes
    # nothing), the zero-rate month is then dropped by the downtime filter,
    # and the fit proceeds. Wells with no gaps are unaffected (no-op fill).
    _numeric = [
        "oil_bbl", "gas_mcf", "water_bbl", "producing_days",
        "rate_calday_bopd", "rate_calday_mcfd", "rate_calday_bwpd",
    ]
    df[_numeric] = df[_numeric].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return df


def _persist(
    session: Session,
    *,
    api10: str,
    stream: str,
    result: ForecastResult,
) -> uuid.UUID:
    """Upsert (api10, stream) → forecasts. Honors `locked` if a prior row
    exists — locked forecasts are skipped so a bulk re-fit doesn't wipe
    a user's manual override."""
    existing = session.execute(
        select(Forecast).where(
            Forecast.api10 == api10, Forecast.stream == Stream(stream)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.locked:
        log.info("forecast_locked_skip", api10=api10, stream=stream)
        return existing.id

    values = {
        "api10": api10,
        "stream": stream,
        "model_type": result.model_type,
        "params": result.params,
        "qi": result.qi,
        "di_initial": result.di_initial,
        "b": result.b,
        "df_terminal": result.df_terminal,
        "qo": result.qo,
        "peak_index_months": result.peak_index_months,
        "eur": result.eur,
        "peak_month_date": result.peak_month_date,
        "peak_rate": result.peak_rate,
        "fit_method": result.fit_method,
        "fit_r2": result.fit_r2,
        "fit_rmse": result.fit_rmse,
        "downtime_ratio": result.downtime_ratio,
        "manual_override": False,
        "locked": False,
        "updated_at": datetime.now(timezone.utc),
    }
    if existing is None:
        values["id"] = uuid.uuid4()
        values["created_at"] = datetime.now(timezone.utc)

    stmt = pg_insert(Forecast.__table__).values(**values)
    update_cols = {
        c: stmt.excluded[c]
        for c in values
        if c not in {"id", "api10", "stream", "created_at", "manual_override", "locked"}
    }
    stmt = stmt.on_conflict_do_update(
        constraint="uq_forecasts_api10_stream", set_=update_cols
    )
    session.execute(stmt)
    session.commit()
    return values.get("id") or existing.id  # type: ignore[union-attr]


def forecast_well(
    session: Session,
    api10: str,
    *,
    config: ForecastConfig | None = None,
    persist: bool = True,
) -> dict[str, ForecastResult | None]:
    """Fit all three streams for a single well.

    Oil and gas anchor on the oil peak; water anchors on its own peak
    (see ``detect_stream_peaks``). Each stream's ramp prefix is anchored
    on its own ONSET (first producing month) rather than the well's
    first-prod, so leading zero / sub-floor months don't inflate the ramp
    length or the type-curve timing (``onset_index_months`` records the
    offset; see ``detect_onset``). Returns a dict {stream: ForecastResult
    or None on fit failure}. When `persist=True` (default), each
    successful result is written to the forecasts table via upsert.
    """
    cfg = config or ForecastConfig()
    # Basin-aware terminal Df: Midland wells get the shallower tail. Look
    # up the well's sub-basin once and bake the chosen Df into the config
    # used for every stream's fit + EUR.
    subbasin = session.execute(
        select(Well.subbasin).where(Well.api10 == api10)
    ).scalar_one_or_none()
    cfg = replace(cfg, df_terminal_per_year=df_terminal_for_subbasin(subbasin, cfg))

    monthly = _load_monthly(session, api10)
    out: dict[str, ForecastResult | None] = {"oil": None, "gas": None, "water": None}
    if monthly.empty:
        log.warning("no_production", api10=api10)
        return out

    # Per-stream peak: oil and gas share the oil peak; water gets its own
    # (flowback peaks months before oil — see detect_stream_peaks).
    peaks = detect_stream_peaks(monthly)
    oil_peak = peaks["oil"]
    if oil_peak is None:
        log.warning("no_oil_peak", api10=api10)
        return out

    # Default "rate_cum" runs the wrapper that retries with rate-time
    # when Di pins at a bound (cum-fit's low b-sensitivity often leaves
    # b at 1.0 and absorbs misfit into Di). Explicit "rate_time" or
    # "rate_cum_strict" opt out — useful for tests and per-well overrides.
    if cfg.fit_method == "rate_time":
        fit_fn = fit_rate_time
    elif cfg.fit_method == "rate_cum_strict":
        fit_fn = fit_rate_cum
    else:
        fit_fn = fit_with_fallback

    for stream in STREAMS:
        # Each stream is fit and ramp-anchored against ITS OWN peak.
        # peak_index_months (the ramp length) is therefore per-stream:
        # oil/gas measure from the oil peak, water from the water peak.
        peak = peaks[stream] or oil_peak
        peak_index_abs = int(peak.peak_index)
        try:
            result = fit_fn(
                monthly,
                model_type=cfg.model_type,
                peak=peak,
                stream=stream,
                config=cfg,
            )
        except Exception as e:  # noqa: BLE001 — fit failures are routine
            log.exception("fit_failed", api10=api10, stream=stream, err=str(e))
            continue

        # Gas inherits oil's peak MONTH but not oil's peak RATE — rewrite
        # to the gas rate at that month so the detail modal shows the
        # right anchor value. Oil and water already carry the correct
        # per-stream peak_rate from their own detected peak (the fit set
        # result.peak_rate = peak.peak_rate, which is in the stream's
        # own units).
        if stream == "gas":
            result = replace(
                result, peak_rate=stream_rate_at_peak(monthly, peak, stream)
            )

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
        result = replace(
            result,
            params=new_params,
            qo=qo,
            peak_index_months=peak_index_months if peak_index_months > 0 else None,
            eur=total_eur,
        )

        out[stream] = result
        if persist:
            _persist(session, api10=api10, stream=stream, result=result)

    return out


_STREAM_RATE_COL: dict[str, str] = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_mcfd",
    "water": "rate_calday_bwpd",
}


def stream_rate_at_peak(
    monthly: pd.DataFrame, peak: PeakResult, stream: str
) -> float:
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


def _stream_rate_at_index(
    monthly: pd.DataFrame, index: int, stream: str
) -> float | None:
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
    app.forecasting.ramp_arps.evaluate_well_rate.
    """
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if df.empty:
        return None
    val = df.iloc[0][_STREAM_RATE_COL[stream]]
    if val is None or pd.isna(val):
        return None
    return float(val)


def forecast_wells(
    session: Session,
    api10s: Iterable[str],
    *,
    config: ForecastConfig | None = None,
) -> dict[str, dict[str, ForecastResult | None]]:
    """Bulk-forecast helper for the batch endpoint."""
    results: dict[str, dict[str, ForecastResult | None]] = {}
    for api10 in api10s:
        results[api10] = forecast_well(session, api10, config=config)
    return results
