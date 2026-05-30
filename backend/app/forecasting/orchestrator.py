"""Run forecasts for one or many wells across oil/gas/water streams.

Glue between the math (`fit.py`) and the DB (`forecasts` table). Key
domain rule: gas/water inherit oil's peak month — we don't detect a
separate peak per stream, because the streams need to share a common t=0
for forecasts and type-curve aggregation to be coherent (per the brief).
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
from app.db.models import Forecast, ProductionMonthly, Stream
from app.forecasting.fit import (
    STREAM_ECON_LIMIT_FIELD,
    fit_rate_cum,
    fit_rate_time,
    fit_with_fallback,
)
from app.forecasting.peak_detection import PeakResult, detect_oil_peak
from app.forecasting.ramp_arps import compute_total_eur
from app.forecasting.types import ForecastConfig, ForecastResult

log = get_logger("forecasting.orchestrator")

STREAMS: tuple[str, ...] = ("oil", "gas", "water")


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
    return pd.DataFrame(rows, columns=[
        "prod_date", "oil_bbl", "gas_mcf", "water_bbl", "producing_days",
        "rate_calday_bopd", "rate_calday_mcfd", "rate_calday_bwpd",
    ])


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
    """Fit all three streams for a single well; gas/water inherit oil peak.

    Returns a dict {stream: ForecastResult or None on fit failure}. When
    `persist=True` (default), each successful result is written to the
    forecasts table via upsert.
    """
    cfg = config or ForecastConfig()
    monthly = _load_monthly(session, api10)
    out: dict[str, ForecastResult | None] = {"oil": None, "gas": None, "water": None}
    if monthly.empty:
        log.warning("no_production", api10=api10)
        return out

    oil_peak = detect_oil_peak(monthly)
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

    # Linear-ramp prefix shared across streams: peak_index_months is
    # measured against the OIL peak (gas/water inherit it by
    # convention). Per-stream qo comes from each stream's first-prod
    # observation. See app.forecasting.ramp_arps.
    peak_index_months = int(oil_peak.peak_index)

    for stream in STREAMS:
        # Use oil's peak for ALL streams — they share t=0 by construction.
        try:
            result = fit_fn(
                monthly,
                model_type=cfg.model_type,
                peak=oil_peak,
                stream=stream,
                config=cfg,
            )
        except Exception as e:  # noqa: BLE001 — fit failures are routine
            log.exception("fit_failed", api10=api10, stream=stream, err=str(e))
            continue

        # Gas/water shouldn't inherit oil's peak RATE — that'd be wrong;
        # rewrite the result's peak_rate to the stream-specific rate at the
        # peak month so the detail modal shows the right anchor value.
        if stream != "oil":
            stream_peak_rate = stream_rate_at_peak(monthly, oil_peak, stream)
            result = replace(result, peak_rate=stream_peak_rate)

        # Stamp the ramp params. params dict also carries them so the
        # evaluator can pick them up without the row-level fields.
        # EUR is recomputed as ramp_eur + arps_eur to match the
        # ramp+Arps model — _build_result's Arps-only EUR was right
        # for the fit math but doesn't reflect the full forecast.
        qo = stream_rate_at_first_prod(monthly, stream)
        new_params = dict(result.params)
        if qo is not None:
            new_params["qo"] = qo
        if peak_index_months > 0:
            new_params["peak_index_months"] = peak_index_months
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
    monthly: pd.DataFrame, oil_peak: PeakResult, stream: str
) -> float:
    """Return the stream's calday rate at the oil-peak month.

    Public helper — the cohort-transfer endpoint anchors short-history
    wells' qi with this same value, so the semantic needs to match the
    per-stream qi the autoforecast picks. 0.0 when the row is missing
    or the rate is null.
    """
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if oil_peak.peak_index >= len(df):
        return 0.0
    val = df.iloc[oil_peak.peak_index][_STREAM_RATE_COL[stream]]
    return float(val) if val is not None and not pd.isna(val) else 0.0


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
