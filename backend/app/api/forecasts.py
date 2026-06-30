"""Forecast API.

  POST /api/forecasts/batch
      body: {api10s: [...], config?: {...}}
      → 202; runs in background; poll /api/sync/status for the SyncJob row.

  GET  /api/forecasts?api10=...
      → list per-stream forecasts for one or more wells.

  GET  /api/forecasts/{id}
      → single forecast.

  PATCH /api/forecasts/{id}
      body: {params?: {...}, locked?: bool, manual_override?: bool}
      → mutate fit params; recompute EUR; mark `manual_override=True`.

  GET  /api/forecasts/{api10}/curves
      → for the detail modal: history (rate + cum) + forecast (rate + cum)
        per stream, plus the prodday rates so the toggle works.

  POST /api/forecasts/preview
      body: {model_type, params, df_terminal?, horizon_years?, econ_limit?}
      → server-side curve evaluation for live re-render when the user edits
        qi/Di/b/Df in the modal.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    FitMethod,
    Forecast,
    ModelType,
    NoviForecastMonthly,
    ProductionMonthly,
    Stream,
    SyncEntity,
    SyncJob,
    SyncJobStatus,
    Well,
)
from app.db.session import SessionLocal, get_session
from app.forecasting.cohort import (
    HistoryPartition,
    compute_donor_medians,
    load_stream_donors,
    partition_by_history,
)
from app.forecasting.eur import DAYS_PER_YEAR, compute_eur
from app.forecasting.fit import (
    STREAM_DOWNTIME_FLOOR_FIELD,
    STREAM_RATE_COLUMN,
    STREAM_VOLUME_COLUMN,
    _flag_downtime,
    _stream_di_hi,
    detect_at_bound,
)
from app.forecasting.metrics import effective_decline_first_year
from app.forecasting.models import (
    arps_exponential,
    arps_harmonic,
    arps_hyperbolic,
    modified_hyperbolic,
)
from app.forecasting.orchestrator import (
    STREAMS,
    _stream_rate_at_index,
    detect_stream_peaks,
    forecast_wells,
    stream_rate_at_peak,
)
from app.forecasting.peak_detection import detect_onset
from app.forecasting.ramp_arps import (
    compute_total_eur,
    evaluate_well_rate,
    model_cum_at_t,
)
from app.forecasting.types import (
    DEFAULT_ECONOMIC_LIMIT_BOPD,
    DEFAULT_ECONOMIC_LIMIT_BWPD,
    DEFAULT_ECONOMIC_LIMIT_MCFD,
    DEFAULT_FORECAST_HORIZON_YEARS,
    ForecastConfig,
)

router = APIRouter(prefix="/forecasts", tags=["forecasts"])
log = get_logger("api.forecasts")

# Monthly→volume conversion for the chart cum series in this module.
# DAYS_PER_YEAR / 12 — same convention as the fit / EUR math and the
# shared display-EUR helper (ramp_arps.trapezoid_eur), so on-screen
# cums converge to the displayed EURs. (Was 30.4375 = 365.25/12, a
# 0.07% drift vs everything the fits integrate.)
_DAYS_PER_MONTH = DAYS_PER_YEAR / 12.0

# ============================ pydantic schemas ============================


class ForecastConfigBody(BaseModel):
    model_type: str = "modified_hyperbolic"
    fit_method: str = "rate_cum"
    df_terminal_per_year: float = Field(default=0.08, ge=0.0, le=0.5)
    horizon_years: float = Field(default=DEFAULT_FORECAST_HORIZON_YEARS, gt=0.0, le=100.0)
    # Economic limits default to the canonical 0.0 from app.forecasting
    # .types — this tool is TECHNICAL-only and EUR is the raw 50-yr
    # integral (economics happens downstream on the export). These used
    # to default to 30 MCFD / 50 BWPD here, which silently truncated
    # gas/water EURs on the batch path only, making stored EURs
    # path-dependent (a Save Override with identical params recomputed
    # at limit 0 and the EUR jumped). Migration 0020 repaired the
    # affected rows. Keep these in lockstep with ForecastConfig —
    # test_technical_eur_defaults.py pins the equality.
    economic_limit_bopd: float = Field(default=DEFAULT_ECONOMIC_LIMIT_BOPD, ge=0.0)
    economic_limit_mcfd: float = Field(default=DEFAULT_ECONOMIC_LIMIT_MCFD, ge=0.0)
    economic_limit_bwpd: float = Field(default=DEFAULT_ECONOMIC_LIMIT_BWPD, ge=0.0)
    # Absolute downtime floor per stream. A post-peak month below this
    # rate gets dropped from the fit regardless of context — catches
    # the choppy-restart pattern that the rolling-max relative check
    # misses (residual rates clustered around zero drag the local max
    # down, masking the dips).
    downtime_floor_bopd: float = Field(default=5.0, ge=0.0)
    downtime_floor_mcfd: float = Field(default=30.0, ge=0.0)
    downtime_floor_bwpd: float = Field(default=10.0, ge=0.0)
    min_post_peak_months: int = Field(default=6, ge=1, le=24)
    # When set, the batch endpoint partitions the well list and fits
    # only the long-history group; short-history wells are returned in
    # the response so the UI can prompt the user to run the cohort
    # transfer after they've reviewed the long fits. Null = legacy path
    # (fit everyone unconstrained).
    short_history_cutoff_months: int | None = Field(default=None, ge=1, le=24)

    def to_config(self) -> ForecastConfig:
        return ForecastConfig(
            model_type=self.model_type,
            fit_method=self.fit_method,
            df_terminal_per_year=self.df_terminal_per_year,
            horizon_years=self.horizon_years,
            economic_limit_bopd=self.economic_limit_bopd,
            economic_limit_mcfd=self.economic_limit_mcfd,
            economic_limit_bwpd=self.economic_limit_bwpd,
            downtime_floor_bopd=self.downtime_floor_bopd,
            downtime_floor_mcfd=self.downtime_floor_mcfd,
            downtime_floor_bwpd=self.downtime_floor_bwpd,
            min_post_peak_months=self.min_post_peak_months,
            short_history_cutoff_months=self.short_history_cutoff_months,
        )


class BatchRequest(BaseModel):
    api10s: list[str] = Field(min_length=1, max_length=500)
    config: ForecastConfigBody | None = None


class BatchResponse(BaseModel):
    accepted: bool
    job_id: uuid.UUID
    well_count: int
    # Populated when the request set `short_history_cutoff_months`.
    # `long_api10s` is what the background task actually fits;
    # `short_api10s` and `no_peak_api10s` are returned so the UI can
    # render a "X pending transfer" banner without re-querying. Null
    # on legacy callers that don't pass the cutoff.
    long_api10s: list[str] | None = None
    short_api10s: list[str] | None = None
    no_peak_api10s: list[str] | None = None


class ForecastRow(BaseModel):
    id: uuid.UUID
    api10: str
    stream: Stream
    model_type: ModelType
    params: dict[str, Any]
    qi: float | None
    di_initial: float | None
    di_effective: float | None  # derived; first-year fraction (0–1)
    b: float | None
    df_terminal: float | None
    # Ramp prefix — see app.forecasting.ramp_arps. NULL on rows that
    # pre-date the columns and haven't been re-fit.
    qo: float | None
    peak_index_months: int | None
    eur: float | None
    peak_month_date: date | None
    peak_rate: float | None
    fit_method: FitMethod
    fit_r2: float | None
    fit_rmse: float | None
    fit_at_bound: bool          # one or more fit parameters pinned at a bound
    bound_note: str | None      # human-readable details when fit_at_bound is True
    # Fraction of post-peak months excluded as downtime (0.0–1.0). High
    # values signal the engineer should eyeball the fit since the well
    # was offline a lot. None on rows that predate the column.
    downtime_ratio: float | None
    # Audit payload — populated for non-standard fit methods. Today
    # that's just cohort_transfer rows; other fit_methods get None.
    # Shape for cohort_transfer: {source, stream, donor_count,
    # donor_api10s, cohort_Di, cohort_b, cutoff_months}.
    diagnostics: dict[str, Any] | None
    manual_override: bool
    locked: bool
    updated_at: datetime
    # Well attributes — populated by /list when joined, None on /by-id.
    # Lets the review page render lateral_ft / formation / vintage in one
    # round-trip instead of N+1 wells lookups.
    well_name: str | None = None
    well_operator: str | None = None
    well_formation: str | None = None
    well_lateral_ft: float | None = None
    well_vintage_year: int | None = None
    # ISO date string. The Review tab shows this directly; vintage_year
    # is the legacy field kept for the map-tab vintage histogram which
    # still needs an integer year for bucketing.
    well_first_prod_date: date | None = None
    well_county: str | None = None
    # Novi's 50-yr oil EUR — benchmark column shown alongside the app's
    # autoforecast EUR in the Review table. Sourced from
    # curated.wells_enriched.eur_50yr_oil_bbl during well sync.
    well_novi_oil_eur: float | None = None
    # Method-1 EUR triple — derived per request from production
    # aggregates so we don't store stale snapshots. See
    # _compute_method_one_metrics for the math.
    #   actual_cum     — total per-stream volume produced to date
    #   eur_remaining  — model EUR minus model cum at end-of-history
    #                    (the model's projection from now → year 50)
    #   eur_displayed  — actual_cum + eur_remaining (the EUR reported
    #                    everywhere user-facing: past is real, future is model)
    actual_cum: float | None = None
    eur_remaining: float | None = None
    eur_displayed: float | None = None

    @classmethod
    def from_orm_row(cls, f: Forecast) -> "ForecastRow":
        di_eff = (
            effective_decline_first_year(f.di_initial, f.b)
            if f.di_initial is not None
            else None
        )
        at_bound, bound_note = (False, None)
        if f.qi is not None and f.di_initial is not None and f.peak_rate is not None:
            # Per-stream Di cap so steep water (cap 12.0) isn't flagged
            # against the oil-tuned 4.0. Uses default config bounds — the
            # fit-time config isn't persisted, but the badge is a QC hint
            # and the defaults are what the autoforecast uses.
            at_bound, bound_note = detect_at_bound(
                qi=f.qi, di=f.di_initial, b=f.b, peak_rate=f.peak_rate,
                di_hi=_stream_di_hi(f.stream.value, ForecastConfig()),
            )
        return cls(
            id=f.id, api10=f.api10, stream=f.stream, model_type=f.model_type,
            params=f.params, qi=f.qi, di_initial=f.di_initial,
            di_effective=di_eff, b=f.b,
            df_terminal=f.df_terminal,
            qo=f.qo, peak_index_months=f.peak_index_months,
            eur=f.eur,
            peak_month_date=f.peak_month_date, peak_rate=f.peak_rate,
            fit_method=f.fit_method, fit_r2=f.fit_r2, fit_rmse=f.fit_rmse,
            fit_at_bound=at_bound, bound_note=bound_note,
            downtime_ratio=f.downtime_ratio,
            diagnostics=f.diagnostics,
            manual_override=f.manual_override, locked=f.locked,
            updated_at=f.updated_at,
        )


class PatchRequest(BaseModel):
    params: dict[str, float] | None = None
    locked: bool | None = None
    manual_override: bool | None = None
    economic_limit: float | None = None


class StreamCurves(BaseModel):
    stream: Stream
    months: list[date]                  # prod_date per history month
    history_rate: list[float | None]    # rate_calday_*
    history_cum: list[float | None]
    # Downtime-filtered variants for slide spaghetti — same shape as
    # history_rate / history_cum but with post-peak months flagged by
    # _flag_downtime nulled out, and the cum re-integrated by linear
    # interpolation across those nulls. Used by SlideRateChart so the
    # observed spaghetti is comparable apples-to-apples with the
    # TC forecast (which was fit without downtime contribution).
    history_rate_filtered: list[float | None]
    history_cum_filtered: list[float | None]
    forecast_months: list[date]         # extended into the future
    forecast_rate: list[float]
    forecast_cum: list[float]
    # Novi's forecasted (PDP) series for this stream, from the synced
    # novi_forecast_monthly table. Rendered as a light-blue benchmark
    # overlay in the detail modal. Empty when the well has no Novi
    # forecast on file. novi_cum is carried for the optional cum overlay.
    novi_months: list[date]
    novi_rate: list[float | None]
    novi_cum: list[float | None]


class WellCurvesResponse(BaseModel):
    api10: str
    streams: list[StreamCurves]


class PreviewRequest(BaseModel):
    model_type: str = "modified_hyperbolic"
    # params carries the full forecast parameter set: qi, Di, b, Df,
    # and optionally qo + peak_index_months for the ramp prefix. When
    # the latter two are absent the evaluator falls back to pure Arps
    # — preserves the pre-ramp preview behavior for callers that
    # don't know about ramp.
    params: dict[str, float]
    economic_limit: float = DEFAULT_ECONOMIC_LIMIT_BOPD
    horizon_years: float = DEFAULT_FORECAST_HORIZON_YEARS
    n_points: int = Field(default=120, ge=10, le=1000)
    # Optional anchor for the Method-1 displayed EUR. When provided,
    # the backend looks up the well's production aggregate and stream
    # and returns eur_displayed + eur_remaining alongside the raw
    # model integral. Without these, the response carries only the
    # canonical model EUR (back-compat for callers that don't need
    # Method-1 — e.g. TC-level previews).
    api10: str | None = None
    stream: Stream | None = None


class PreviewResponse(BaseModel):
    t_years: list[float]
    rate: list[float]
    cum: list[float]
    eur: float
    # Method-1 metrics. Populated only when the request carried api10
    # + stream so the backend could resolve the well's actual cum and
    # last-observed month for the previewed params.
    eur_displayed: float | None = None
    eur_remaining: float | None = None
    actual_cum: float | None = None


# ============================ batch endpoint ============================


def _batch_bg(api10s: list[str], cfg: ForecastConfig, job_id: uuid.UUID) -> None:
    with SessionLocal() as session:
        job = session.get(SyncJob, job_id)
        if job is None:
            return
        job.status = SyncJobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        session.commit()
        try:
            forecast_wells(session, api10s, config=cfg)
            job.items_seen = len(api10s)
            job.items_upserted = len(api10s)
            job.status = SyncJobStatus.SUCCEEDED
        except Exception as e:  # noqa: BLE001 — record + raise out of bg
            session.rollback()
            job_row = session.get(SyncJob, job_id)
            if job_row is not None:
                job_row.status = SyncJobStatus.FAILED
                job_row.error = str(e)[:2000]
                job_row.finished_at = datetime.now(timezone.utc)
                session.commit()
            log.exception("batch_forecast_failed", job_id=str(job_id))
            return
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


@router.post("/batch", response_model=BatchResponse, status_code=202)
def batch_forecast(
    req: BatchRequest, background: BackgroundTasks
) -> BatchResponse:
    cfg = (req.config or ForecastConfigBody()).to_config()
    job_id = uuid.uuid4()
    # Partition synchronously when the user asked for cohort-transfer.
    # This costs one production_monthly query per well — for the
    # 500-well batch cap that's ~1-2s, comfortably inside the request
    # window. The alternative (partition inside the bg task) would
    # force the UI to poll something extra to discover the split
    # before it can render the "X pending transfer" banner.
    partition: HistoryPartition | None = None
    if cfg.short_history_cutoff_months is not None:
        with SessionLocal() as session:
            partition = partition_by_history(
                session, list(req.api10s), cfg.short_history_cutoff_months
            )
    api10s_to_fit = (
        partition.long_api10s if partition is not None else list(req.api10s)
    )
    # Use the existing sync_jobs table for batch-job tracking. SyncEntity
    # doesn't have a "forecast" value yet; "well_headers" is the closest
    # bucket but we shouldn't pretend that's what this is. Tag it via metadata.
    with SessionLocal() as session:
        session.add(SyncJob(
            id=job_id,
            entity=SyncEntity.WELL_HEADERS,
            scope_key=f"forecast:{len(api10s_to_fit)}_wells",
            status=SyncJobStatus.PENDING,
            metadata_={
                "kind": "forecast_batch",
                "api10_count": len(api10s_to_fit),
                "short_history_cutoff_months": cfg.short_history_cutoff_months,
            },
        ))
        session.commit()
    background.add_task(_batch_bg, api10s_to_fit, cfg, job_id)
    return BatchResponse(
        accepted=True,
        job_id=job_id,
        well_count=len(api10s_to_fit),
        long_api10s=partition.long_api10s if partition is not None else None,
        short_api10s=partition.short_api10s if partition is not None else None,
        no_peak_api10s=partition.no_peak_api10s if partition is not None else None,
    )


# ============================ cohort transfer ============================


class TransferRequest(BaseModel):
    api10s: list[str] = Field(min_length=1, max_length=500)
    short_history_cutoff_months: int = Field(default=6, ge=1, le=24)
    # Refuse to transfer when the long-history cohort is thinner than
    # this. Computed against the oil donor pool; gas/water inherit the
    # check by being members of the same long set. 5 is a low bar but
    # the user is best positioned to widen it via the request body.
    min_donor_count: int = Field(default=5, ge=1, le=50)
    config: ForecastConfigBody | None = None


class TransferStreamDonor(BaseModel):
    stream: Stream
    donor_count: int
    cohort_di: float
    cohort_b: float


class TransferResponse(BaseModel):
    written_api10s: list[str]
    skipped_locked: list[tuple[str, Stream]]
    skipped_no_peak: list[str]
    long_api10s: list[str]
    short_api10s: list[str]
    donors: list[TransferStreamDonor]


def _load_monthly_for_transfer(session: Session, api10: str):
    """Minimal frame for peak detection + per-stream qi lookup."""
    import pandas as pd  # local import keeps the top of file lean

    rows = session.execute(
        select(
            ProductionMonthly.prod_date,
            ProductionMonthly.rate_calday_bopd,
            ProductionMonthly.rate_calday_mcfd,
            ProductionMonthly.rate_calday_bwpd,
        )
        .where(ProductionMonthly.api10 == api10)
        .order_by(ProductionMonthly.prod_date)
    ).all()
    return pd.DataFrame(
        rows,
        columns=[
            "prod_date",
            "rate_calday_bopd",
            "rate_calday_mcfd",
            "rate_calday_bwpd",
        ],
    )


_STREAM_ECON_LIMIT_FIELD: dict[str, str] = {
    "oil": "economic_limit_bopd",
    "gas": "economic_limit_mcfd",
    "water": "economic_limit_bwpd",
}

# Per-stream cumulative column on novi_forecast_monthly (the rate column
# is the shared STREAM_RATE_COLUMN). Used by the /curves Novi overlay.
_STREAM_NOVI_CUM_COL: dict[str, str] = {
    "oil": "cumulative_oil_bbl",
    "gas": "cumulative_gas_mcf",
    "water": "cumulative_water_bbl",
}

# Per-stream absolute downtime floor for the /curves endpoint's
# filtered history series. Matches the orchestrator's defaults
# (see ForecastConfig.downtime_floor_*). Hardcoded here because the
# /curves endpoint isn't config-aware; the slide just needs a
# defensible filter for the spaghetti, not user-tunable knobs.
_STREAM_DOWNTIME_FLOOR_FOR_CURVES: dict[str, float] = {
    "oil": 5.0,
    "gas": 30.0,
    "water": 10.0,
}


def _filtered_rate_and_cum(
    history_rate: list[float | None],
    history_cum: list[float | None],
    *,
    peak_idx: int | None,
    absolute_floor: float,
) -> tuple[list[float | None], list[float | None]]:
    """Return (rate_filtered, cum_filtered) where downtime months from
    peak forward are nulled in the rate series and the cum is re-
    integrated via trapezoid on the non-null rates only — same as
    drawing a continuous line through the filtered samples.

    Pre-peak months are passed through unchanged (ramp months don't
    get the downtime treatment because they're naturally low). When
    peak_idx is None or out of range, every month is passed through.
    """
    n = len(history_rate)
    if peak_idx is None or peak_idx >= n:
        return list(history_rate), list(history_cum)

    rate_filtered: list[float | None] = list(history_rate)
    # Apply the filter only post-peak (inclusive of the peak month).
    post_peak = pd.Series(
        [float(v) if v is not None else 0.0 for v in history_rate[peak_idx:]],
        dtype=float,
    )
    mask = _flag_downtime(post_peak, absolute_floor=absolute_floor)
    for j, is_downtime in enumerate(mask):
        if bool(is_downtime):
            rate_filtered[peak_idx + j] = None

    # Re-integrate cum from the filtered rates using a trapezoid step
    # between consecutive non-null samples. _DAYS_PER_MONTH matches the
    # convention everywhere else in this file.
    cum_filtered: list[float | None] = [None] * n
    running = 0.0
    last_idx: int | None = None
    last_rate: float | None = None
    for i in range(n):
        ri = rate_filtered[i]
        if ri is None:
            cum_filtered[i] = None
            continue
        if last_idx is not None and last_rate is not None:
            running += 0.5 * (last_rate + ri) * _DAYS_PER_MONTH * (i - last_idx)
        cum_filtered[i] = running
        last_idx = i
        last_rate = ri
    return rate_filtered, cum_filtered


@router.post("/transfer-cohort-params", response_model=TransferResponse)
def transfer_cohort_params(
    req: TransferRequest, session: Session = Depends(get_session)
) -> TransferResponse:
    """Write transfer forecasts for short-history wells in a batch.

    Reads the resolved (auto-fit or user-edited) Di / b of the
    long-history wells in the same batch, takes the per-stream median,
    and writes a forecast row per short well using its own qi (rate at
    that stream's peak month — water on its own early peak, oil/gas on
    the oil peak) plus the cohort medians. Honors `locked` on existing
    short-side rows — a locked row is preserved.

    Returns HTTP 422 when the oil donor pool is thinner than
    `min_donor_count`; no writes occur in that case. The user widens
    the batch (or waits for more wells to accrue history) and retries.
    """
    cfg = (req.config or ForecastConfigBody()).to_config()
    partition = partition_by_history(
        session, list(req.api10s), req.short_history_cutoff_months
    )

    # Per-stream donor medians from the long cohort.
    donor_summaries: list[TransferStreamDonor] = []
    medians_by_stream: dict[str, tuple[float, float, int, list[str]]] = {}
    for stream_str in STREAMS:
        stream_enum = Stream(stream_str)
        donors = load_stream_donors(session, partition.long_api10s, stream_enum)
        med = compute_donor_medians(donors)
        if med is None:
            continue
        medians_by_stream[stream_str] = (
            med.di, med.b, med.donor_count, med.donor_api10s
        )
        donor_summaries.append(TransferStreamDonor(
            stream=stream_enum,
            donor_count=med.donor_count,
            cohort_di=med.di,
            cohort_b=med.b,
        ))

    # Gate on oil — that's the stream whose Di degeneracy drove the
    # feature. Gas/water inherit the check by virtue of being in the
    # same long cohort. If oil donors are too thin, the whole batch
    # bails — the user knows to widen the selection.
    oil_donor_count = medians_by_stream.get("oil", (0.0, 0.0, 0, []))[2]
    if oil_donor_count < req.min_donor_count:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_donor_cohort",
                "donor_count": oil_donor_count,
                "min_required": req.min_donor_count,
                "long_api10s": partition.long_api10s,
                "short_api10s": partition.short_api10s,
                "no_peak_api10s": partition.no_peak_api10s,
            },
        )

    written: list[str] = []
    skipped_locked: list[tuple[str, Stream]] = []
    skipped_no_peak: list[str] = list(partition.no_peak_api10s)

    for api10 in partition.short_api10s:
        monthly = _load_monthly_for_transfer(session, api10)
        if monthly.empty:
            skipped_no_peak.append(api10)
            continue
        peaks = detect_stream_peaks(monthly)
        if peaks["oil"] is None:
            skipped_no_peak.append(api10)
            continue

        any_written_for_well = False
        for stream_str in STREAMS:
            if stream_str not in medians_by_stream:
                # No donor data for this stream — leave the short well
                # without a row here. Rare in practice (Permian wells
                # produce all three streams) but possible if every
                # long-cohort well lacked a same-stream forecast.
                continue
            cohort_di, cohort_b, donor_count, donor_api10s = (
                medians_by_stream[stream_str]
            )
            stream_enum = Stream(stream_str)

            existing = session.execute(
                select(Forecast).where(
                    Forecast.api10 == api10, Forecast.stream == stream_enum
                )
            ).scalar_one_or_none()
            if existing is not None and existing.locked:
                skipped_locked.append((api10, stream_enum))
                continue

            # Anchor on THIS stream's own peak — same per-stream
            # convention as the autoforecast (every stream detects its
            # own; gas commonly peaks after oil). A stream with no real
            # production has no peak and is skipped: a transfer row
            # with qi=0 would be a phantom forecast.
            stream_peak = peaks[stream_str]
            if stream_peak is None:
                continue
            qi = stream_rate_at_peak(monthly, stream_peak, stream_str)
            # Ramp prefix is well-specific even on a cohort transfer, and
            # anchored on the stream's ONSET (first producing month) — qo
            # is the onset rate and peak_index_months is peak-minus-onset,
            # so leading sub-floor months don't inflate the ramp / TC
            # timing. Only Di/b are inherited from the cohort median.
            floor = getattr(cfg, STREAM_DOWNTIME_FLOOR_FIELD[stream_str])
            onset_index = min(
                detect_onset(
                    monthly, rate_column=STREAM_RATE_COLUMN[stream_str], floor=floor
                ),
                int(stream_peak.peak_index),
            )
            qo = _stream_rate_at_index(monthly, onset_index, stream_str)
            peak_rel = int(stream_peak.peak_index) - onset_index
            peak_index_months = peak_rel if peak_rel > 0 else None
            params: dict[str, Any] = {
                "qi": qi,
                "Di": cohort_di,
                "b": cohort_b,
                "Df": cfg.df_terminal_per_year,
            }
            if qo is not None:
                params["qo"] = qo
            if peak_index_months is not None:
                params["peak_index_months"] = peak_index_months
            if onset_index > 0:
                params["onset_index_months"] = onset_index
            econ_limit = getattr(cfg, _STREAM_ECON_LIMIT_FIELD[stream_str])
            eur = compute_total_eur(
                model_type="modified_hyperbolic",
                params=params,
                horizon_years=cfg.horizon_years,
                economic_limit=econ_limit,
            )
            diagnostics = {
                "source": "batch_transfer",
                "stream": stream_str,
                "donor_count": donor_count,
                "donor_api10s": donor_api10s,
                "cohort_Di": cohort_di,
                "cohort_b": cohort_b,
                "cutoff_months": req.short_history_cutoff_months,
            }
            values: dict[str, Any] = {
                "api10": api10,
                "stream": stream_str,
                "model_type": "modified_hyperbolic",
                "params": params,
                "qi": qi,
                "di_initial": cohort_di,
                "b": cohort_b,
                "df_terminal": cfg.df_terminal_per_year,
                "qo": qo,
                "peak_index_months": peak_index_months,
                "eur": eur,
                "peak_month_date": stream_peak.peak_month_date,
                # Per-stream peak rate — same convention as the
                # orchestrator's fit-time persistence.
                "peak_rate": qi,
                "fit_method": "cohort_transfer",
                "fit_r2": None,
                "fit_rmse": None,
                "downtime_ratio": None,
                "diagnostics": diagnostics,
                "manual_override": False,
                "locked": False,
                "updated_at": datetime.now(timezone.utc),
            }
            if existing is None:
                values["id"] = uuid.uuid4()
                values["created_at"] = datetime.now(timezone.utc)

            stmt = pg_insert(Forecast.__table__).values(**values)
            # Same provenance rule as orchestrator._persist: a transfer
            # overwrite of an unlocked row resets manual_override to
            # False (machine params → machine provenance); `locked`
            # stays excluded (locked rows are skipped above).
            update_cols = {
                c: stmt.excluded[c]
                for c in values
                if c not in {"id", "api10", "stream", "created_at", "locked"}
            }
            stmt = stmt.on_conflict_do_update(
                constraint="uq_forecasts_api10_stream", set_=update_cols
            )
            session.execute(stmt)
            any_written_for_well = True

        if any_written_for_well:
            written.append(api10)

    session.commit()

    return TransferResponse(
        written_api10s=written,
        skipped_locked=skipped_locked,
        skipped_no_peak=skipped_no_peak,
        long_api10s=partition.long_api10s,
        short_api10s=partition.short_api10s,
        donors=donor_summaries,
    )


# ============================ list / detail / patch ============================


_STREAM_VOL_ATTR = {
    Stream.OIL: "oil_bbl",
    Stream.GAS: "gas_mcf",
    Stream.WATER: "water_bbl",
}


def _months_between(anchor: date, last: date) -> float:
    """Whole-month delta from ``anchor`` to ``last`` (inclusive of last).

    Production rows are first-of-month dates; ``last - anchor`` in
    months is the offset of the last observation from the model's
    t=0. Adding 1 reflects that the well *produced through* that
    month, so the model integral should run to the END of it.
    """
    return float((last.year - anchor.year) * 12 + (last.month - anchor.month) + 1)


def _add_months(d: date, n: int) -> date:
    """First-of-month ``n`` months after ``d`` (n may be 0)."""
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def _apply_method_one(
    row: "ForecastRow",
    f: Forecast,
    well_first_prod_date: date | None,
    actual_cum: float | None,
    last_prod_date: date | None,
) -> None:
    """Populate ``row.actual_cum``/``eur_remaining``/``eur_displayed``.

    Mutates the passed-in ForecastRow rather than returning a new one
    — matches the in-place style used for the other joined ``well_*``
    fields. Falls back to the model EUR when production / anchor data
    is missing so the UI always has a finite value to show.
    """
    row.actual_cum = float(actual_cum) if actual_cum is not None else None
    if f.eur is None:
        row.eur_remaining = None
        row.eur_displayed = None
        return
    # Anchor: with ramp params, model t=0 is at the stream's ONSET
    # (first_prod + onset_index_months); without, at peak_month (the
    # historical anchor for pre-migration rows). onset is 0 for streams
    # with no leading sub-floor months, so this reduces to first_prod.
    has_ramp = (
        f.peak_index_months is not None
        or (f.params or {}).get("peak_index_months") is not None
    )
    onset_m = int((f.params or {}).get("onset_index_months") or 0)
    if has_ramp:
        anchor = (
            _add_months(well_first_prod_date, onset_m)
            if well_first_prod_date is not None
            else None
        )
    else:
        anchor = f.peak_month_date
    if anchor is None or last_prod_date is None or last_prod_date < anchor:
        # No history under this fit → "remaining" is the full EUR;
        # displayed equals model EUR (no actual yet to layer in).
        row.eur_remaining = float(f.eur)
        row.eur_displayed = float(f.eur)
        return
    if f.qi is None or f.di_initial is None or f.b is None or f.df_terminal is None:
        # Fit params incomplete (shouldn't happen for fitted rows, but
        # be defensive). Just report model EUR as displayed.
        row.eur_remaining = float(f.eur)
        row.eur_displayed = float(f.eur)
        return
    t_years = _months_between(anchor, last_prod_date) / 12.0
    model_cum = model_cum_at_t(
        qo=f.qo,
        peak_index_months=f.peak_index_months,
        qi=float(f.qi),
        Di=float(f.di_initial),
        b=float(f.b),
        Df=float(f.df_terminal),
        t_years=t_years,
    )
    remaining = max(0.0, float(f.eur) - model_cum)
    row.eur_remaining = remaining
    row.eur_displayed = float(actual_cum or 0.0) + remaining


@router.get("", response_model=list[ForecastRow])
def list_forecasts(
    api10: list[str] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ForecastRow]:
    """List forecasts joined to wells for the review/forecast grids.

    Single-trip JOIN avoids the N+1 well-lookup the frontend used to do.
    """
    # Per-well per-stream production totals + last-observed date as a
    # single grouped subquery. LEFT JOIN-ed so wells without any
    # production still surface their forecast rows (the helper falls
    # back to the model EUR when actual_cum is None).
    prod_agg = (
        select(
            ProductionMonthly.api10.label("api10"),
            func.sum(ProductionMonthly.oil_bbl).label("oil_cum"),
            func.sum(ProductionMonthly.gas_mcf).label("gas_cum"),
            func.sum(ProductionMonthly.water_bbl).label("water_cum"),
            func.max(ProductionMonthly.prod_date).label("last_prod_date"),
        )
        .group_by(ProductionMonthly.api10)
        .subquery()
    )
    stmt = (
        select(
            Forecast,
            Well.name,
            Well.operator,
            Well.formation_blueox.label("formation"),
            Well.lateral_ft,
            Well.vintage_year,
            Well.first_prod_date,
            Well.county,
            Well.novi_oil_eur,
            prod_agg.c.oil_cum,
            prod_agg.c.gas_cum,
            prod_agg.c.water_cum,
            prod_agg.c.last_prod_date,
        )
        .join(Well, Well.api10 == Forecast.api10)
        .outerjoin(prod_agg, prod_agg.c.api10 == Forecast.api10)
        .order_by(Forecast.api10, Forecast.stream)
    )
    if api10:
        stmt = stmt.where(Forecast.api10.in_(api10))
    out: list[ForecastRow] = []
    for (
        f, name, operator, formation, lateral_ft, vintage_year,
        first_prod_date, county, novi_oil_eur,
        oil_cum, gas_cum, water_cum, last_prod_date,
    ) in session.execute(stmt).all():
        row = ForecastRow.from_orm_row(f)
        # mutate in-place — model_copy(update=) would also work but allocates.
        row.well_name = name
        row.well_operator = operator
        row.well_formation = formation
        row.well_lateral_ft = float(lateral_ft) if lateral_ft is not None else None
        row.well_vintage_year = int(vintage_year) if vintage_year is not None else None
        row.well_first_prod_date = first_prod_date
        row.well_county = county
        row.well_novi_oil_eur = (
            float(novi_oil_eur) if novi_oil_eur is not None else None
        )
        cum_by_stream = {
            Stream.OIL: oil_cum, Stream.GAS: gas_cum, Stream.WATER: water_cum,
        }
        _apply_method_one(
            row, f,
            well_first_prod_date=first_prod_date,
            actual_cum=cum_by_stream.get(f.stream),
            last_prod_date=last_prod_date,
        )
        out.append(row)
    return out


def _row_with_well_join(f: Forecast, session: Session) -> ForecastRow:
    """Build a ForecastRow with the joined well_* fields populated.

    Used by /forecasts/{id} (GET) and PATCH so an edit response carries
    the same shape as /forecasts (list). The Review grid splices the
    response into its in-memory `allForecasts` map; without this helper
    the well headers (name / operator / formation / lateral / vintage)
    would null out on every Save Override / Lock click because
    `from_orm_row` only knows about the Forecast columns.
    """
    row = ForecastRow.from_orm_row(f)
    well = session.get(Well, f.api10)
    if well is not None:
        row.well_name = well.name
        row.well_operator = well.operator
        # Standardized formation (formation_blueox) for display — keep in
        # sync with the /forecasts list endpoint, which also labels
        # formation_blueox as `formation`. Using raw well.formation here
        # would revert the Review row to the un-standardized value on every
        # Save Override / Lock (PATCH splices this response back in-place).
        row.well_formation = well.formation_blueox
        row.well_lateral_ft = (
            float(well.lateral_ft) if well.lateral_ft is not None else None
        )
        row.well_vintage_year = (
            int(well.vintage_year) if well.vintage_year is not None else None
        )
        row.well_first_prod_date = well.first_prod_date
        row.well_county = well.county
        row.well_novi_oil_eur = (
            float(well.novi_oil_eur) if well.novi_oil_eur is not None else None
        )
    # Per-stream production aggregate for the Method-1 EUR computation.
    # One scalar SUM query; cheap on the indexed (api10, prod_date) PK.
    vol_attr = _STREAM_VOL_ATTR[f.stream]
    actual_cum_row = session.execute(
        select(
            func.sum(getattr(ProductionMonthly, vol_attr)),
            func.max(ProductionMonthly.prod_date),
        ).where(ProductionMonthly.api10 == f.api10)
    ).one()
    _apply_method_one(
        row, f,
        well_first_prod_date=well.first_prod_date if well else None,
        actual_cum=actual_cum_row[0],
        last_prod_date=actual_cum_row[1],
    )
    return row


@router.get("/{forecast_id}", response_model=ForecastRow)
def get_forecast(forecast_id: uuid.UUID, session: Session = Depends(get_session)) -> ForecastRow:
    f = session.get(Forecast, forecast_id)
    if f is None:
        raise HTTPException(status_code=404, detail="not found")
    return _row_with_well_join(f, session)


@router.patch("/{forecast_id}", response_model=ForecastRow)
def patch_forecast(
    forecast_id: uuid.UUID,
    req: PatchRequest,
    session: Session = Depends(get_session),
) -> ForecastRow:
    f = session.get(Forecast, forecast_id)
    if f is None:
        raise HTTPException(status_code=404, detail="not found")

    if req.params is not None:
        # Validate then apply. Keep top-level scalar fields in sync with params.
        merged: dict[str, Any] = {**(f.params or {}), **req.params}
        f.params = merged
        f.qi = merged.get("qi", f.qi)
        f.di_initial = merged.get("Di", f.di_initial)
        f.b = merged.get("b", f.b)
        f.df_terminal = merged.get("Df", f.df_terminal)
        # Ramp prefix is editable too — qo and peak_index_months come
        # through req.params from the detail modal's param editor.
        # Mirror them into the denormalized columns so the loader's
        # JOIN-aware queries pick them up without parsing JSONB.
        if "qo" in merged:
            f.qo = merged["qo"]
        if "peak_index_months" in merged:
            f.peak_index_months = (
                int(merged["peak_index_months"])
                if merged["peak_index_months"] is not None
                else None
            )
        # Recompute EUR with the edited params. compute_total_eur picks
        # up qo / peak_index_months if present (ramp + Arps); falls
        # back to pure Arps when those are missing from `merged`.
        econ_limit = req.economic_limit or DEFAULT_ECONOMIC_LIMIT_BOPD
        f.eur = compute_total_eur(
            model_type=f.model_type.value,
            params=merged,
            economic_limit=econ_limit,
        )
        f.manual_override = True  # user-touched
        # Auto-lock on manual edit: an edited-but-unlocked row would be
        # silently overwritten by the next bulk re-fit (the orchestrator
        # only skips LOCKED rows), losing the engineer's params while —
        # pre-fix — even keeping manual_override=True on machine-fit
        # values. Locking here makes "I touched it" mean "re-fit keeps
        # its hands off" without a separate click. An explicit
        # `locked: false` in the same request still wins (applied below).
        f.locked = True

    if req.locked is not None:
        f.locked = req.locked
    if req.manual_override is not None:
        f.manual_override = req.manual_override

    session.commit()
    return _row_with_well_join(f, session)


# ============================ curves (for detail modal) ============================


def _evaluate_rate(model_type: str, params: dict[str, Any], t: np.ndarray) -> np.ndarray:
    """Evaluate the well's rate model on a time grid.

    For ``modified_hyperbolic`` the t-axis is years since FIRST
    PRODUCTION and the evaluator handles the ramp prefix + Arps tail
    via ``evaluate_well_rate``. When the row pre-dates the ramp
    columns and they're missing from ``params``, evaluate_well_rate
    falls back to pure modified-hyperbolic from t=0 — the caller
    (e.g. well_curves) is responsible for anchoring the t=0 point at
    the historically-correct date for that case.

    Other Arps families (exponential / harmonic / hyperbolic) keep
    their original pure-Arps evaluation and are anchored at peak by
    the caller. They aren't the default fit and weren't part of the
    ramp design.
    """
    if model_type == "arps_exponential":
        return arps_exponential(t, params["qi"], params["Di"])
    if model_type == "arps_harmonic":
        return arps_harmonic(t, params["qi"], params["Di"])
    if model_type == "arps_hyperbolic":
        return arps_hyperbolic(t, params["qi"], params["Di"], params["b"])
    if model_type == "modified_hyperbolic":
        return evaluate_well_rate(
            qo=params.get("qo"),
            peak_index_months=params.get("peak_index_months"),
            qi=params["qi"],
            Di=params["Di"],
            b=params["b"],
            Df=params["Df"],
            t_years=t,
        )
    raise HTTPException(status_code=400, detail=f"unsupported model: {model_type}")


@router.get("/{api10}/curves", response_model=WellCurvesResponse)
def well_curves(
    api10: str,
    horizon_years: float = Query(default=DEFAULT_FORECAST_HORIZON_YEARS, gt=0),
    tc_id: uuid.UUID | None = Query(default=None),
    session: Session = Depends(get_session),
) -> WellCurvesResponse:
    """History (months 0..N) + ramp + Arps forecast (t=0 at first prod → horizon) per stream.

    When the forecast row carries ramp params (qo + peak_index_months,
    populated at fit time by the orchestrator), the forecast is
    anchored at the well's first-production month and the rate covers
    the ramp prefix (qo→qi over peak_index_months) before declining via
    Arps. History and forecast share the same first-prod-aligned
    x-axis so the chart renders cleanly without any peak-offset shift.

    For pre-ramp-migration rows where qo / peak_index_months are NULL,
    the forecast still anchors at peak_month_date with rate[0]=qi —
    preserves the older "Months since peak" semantic for those wells
    until they're re-fit.

    When ``tc_id`` is supplied, any per-(api10, stream) override saved
    on that type curve takes precedence over the global forecast row
    for the forecast portion. The detail modal passes this when open
    in TC context so "Save TC override" updates the chart in place
    rather than snapping back to the global fit.
    """
    prod_rows = session.execute(
        select(ProductionMonthly).where(ProductionMonthly.api10 == api10)
        .order_by(ProductionMonthly.prod_date)
    ).scalars().all()
    if not prod_rows:
        raise HTTPException(status_code=404, detail=f"no production for {api10}")

    forecasts = session.execute(
        select(Forecast).where(Forecast.api10 == api10)
    ).scalars().all()
    fc_by_stream = {f.stream: f for f in forecasts}

    # Novi's forecast series for this well — queried once, sliced per
    # stream below. Empty list when the well has no synced Novi forecast.
    novi_rows = session.execute(
        select(NoviForecastMonthly)
        .where(NoviForecastMonthly.api10 == api10)
        .order_by(NoviForecastMonthly.prod_date)
    ).scalars().all()
    novi_months_all = [r.prod_date for r in novi_rows]

    # Optional TC-context override: when present, the forecast portion
    # for any stream with an override switches to those params,
    # leaving the saved global row untouched. Imported locally to
    # keep this module free of TC imports at import time (avoid the
    # api -> type-curves import dependency surfacing as a cycle).
    tc_overrides_for_well: dict[str, dict[str, Any]] = {}
    if tc_id is not None:
        from app.db.models import TypeCurve

        tc_row = session.get(TypeCurve, tc_id)
        if tc_row is not None:
            tc_overrides_for_well = (tc_row.forecast_overrides or {}).get(api10, {}) or {}

    first_prod_date: date = prod_rows[0].prod_date

    streams: list[StreamCurves] = []
    for stream_name in ("oil", "gas", "water"):
        st = Stream(stream_name)
        rate_col = STREAM_RATE_COLUMN[stream_name]
        vol_col = STREAM_VOLUME_COLUMN[stream_name]

        months = [r.prod_date for r in prod_rows]
        history_rate = [getattr(r, rate_col) for r in prod_rows]
        cum = 0.0
        history_cum: list[float | None] = []
        for r in prod_rows:
            v = getattr(r, vol_col)
            cum += float(v) if v is not None else 0.0
            history_cum.append(cum)

        forecast_months: list[date] = []
        forecast_rate: list[float] = []
        forecast_cum: list[float] = []
        fc = fc_by_stream.get(st)
        # Pick the param set + model_type for the forecast portion:
        # the TC override wins when present (tc_id query supplied + a
        # row exists for this stream), otherwise fall back to the
        # global forecast row.
        override_block = tc_overrides_for_well.get(stream_name) or None
        if override_block:
            eval_params = dict(override_block.get("params") or {})
            eval_model_type = str(
                override_block.get("model_type") or "modified_hyperbolic"
            )
        elif fc:
            eval_params = dict(fc.params)
            eval_model_type = fc.model_type.value
        else:
            eval_params = {}
            eval_model_type = "modified_hyperbolic"

        if (fc and fc.peak_month_date is not None) or override_block:
            n_pts = int(horizon_years * 12)
            t = np.arange(n_pts, dtype=float) / 12.0
            # Anchor at the stream's ONSET (first_prod + onset_index_months)
            # when the row has ramp params — for a delayed-onset stream
            # (e.g. water with leading zeros) the forecast then lines up
            # with the real history instead of slow-ramping from month 0.
            # onset defaults to 0, so this reduces to first_prod. peak_month
            # otherwise for back-compat with pre-ramp rows.
            has_ramp = eval_params.get("peak_index_months") is not None and (
                eval_params.get("qo") is not None
            )
            if has_ramp:
                onset_m = int(eval_params.get("onset_index_months") or 0)
                anchor_date = _add_months(first_prod_date, onset_m)
            else:
                # Use the global row's peak when available; the override
                # itself doesn't carry a peak_month_date.
                anchor_date = fc.peak_month_date if fc else first_prod_date
            r = _evaluate_rate(eval_model_type, eval_params, t)
            cum_running = 0.0
            for i in range(n_pts):
                rate_i = float(r[i])
                forecast_rate.append(rate_i)
                # Trapezoid step from t[i-1] to t[i] (one month apart).
                # cum[0] = 0 (no production has accumulated AT the
                # anchor boundary itself). Each subsequent step adds
                # the average of the two end-rates times the month
                # length (_DAYS_PER_MONTH).
                if i > 0:
                    cum_running += 0.5 * (float(r[i - 1]) + rate_i) * _DAYS_PER_MONTH
                forecast_cum.append(cum_running)
                yr = anchor_date.year + (anchor_date.month - 1 + i) // 12
                mo = ((anchor_date.month - 1 + i) % 12) + 1
                forecast_months.append(date(yr, mo, 1))

        history_rate_floats = [
            float(v) if v is not None else None for v in history_rate
        ]
        history_cum_floats = [float(v) for v in history_cum]
        # Downtime-filter the post-peak portion of the observed series
        # so the slide spaghetti reflects only producing months — the
        # forecast was fit with the same filter, so comparing them
        # apples-to-apples is what the analyst needs to QC the TC.
        peak_idx_in_history: int | None = None
        if fc and fc.peak_month_date is not None and fc.peak_month_date in months:
            peak_idx_in_history = months.index(fc.peak_month_date)
        history_rate_filtered, history_cum_filtered = _filtered_rate_and_cum(
            history_rate_floats,
            history_cum_floats,
            peak_idx=peak_idx_in_history,
            absolute_floor=_STREAM_DOWNTIME_FLOOR_FOR_CURVES[stream_name],
        )
        # Novi overlay for this stream. The rate column is the shared
        # rate_calday_*; the cum column is per-stream. novi_months is the
        # same for all three streams (one query), repeated here so each
        # stream's series is self-contained for the frontend.
        novi_cum_col = _STREAM_NOVI_CUM_COL[stream_name]
        novi_rate = [
            float(v) if (v := getattr(r, rate_col)) is not None else None
            for r in novi_rows
        ]
        novi_cum = [
            float(v) if (v := getattr(r, novi_cum_col)) is not None else None
            for r in novi_rows
        ]
        streams.append(StreamCurves(
            stream=st,
            months=months,
            history_rate=history_rate_floats,
            history_cum=history_cum_floats,
            history_rate_filtered=history_rate_filtered,
            history_cum_filtered=history_cum_filtered,
            forecast_months=forecast_months,
            forecast_rate=forecast_rate,
            forecast_cum=forecast_cum,
            novi_months=list(novi_months_all),
            novi_rate=novi_rate,
            novi_cum=novi_cum,
        ))

    return WellCurvesResponse(api10=api10, streams=streams)


# ============================ preview (live re-render) ============================


@router.post("/preview", response_model=PreviewResponse)
def forecast_preview(
    req: PreviewRequest, session: Session = Depends(get_session)
) -> PreviewResponse:
    # Sample from t=0 (peak boundary, rate=qi) through the horizon so
    # rate[0] sits exactly at the peak and cum[0] is 0 — same convention
    # as /api/forecasts/{api10}/curves. The previous version sampled
    # from t=0.01 and integrated with right-endpoint Riemann
    # (`cum += rate * dt`), which UNDERESTIMATES the cum integral of
    # any declining curve — the curves endpoint uses dense monthly
    # trapezoidal integration, so its cum landed ~10% higher than the
    # preview's at the same params and the chart visibly snapped up on
    # Save Override.
    t = np.linspace(0.0, req.horizon_years, req.n_points)
    rates = _evaluate_rate(req.model_type, req.params, t)
    rates_arr = np.asarray(rates, dtype=float)
    # Trapezoidal integration: cum[i] = ∫(0..t[i]) rate dτ.
    # Each step's area = 0.5 * (rate[i-1] + rate[i]) * (t[i]-t[i-1]) * 365.
    # cum[0] = 0 (no production has accumulated at the peak boundary).
    dt = np.diff(t)
    step_area = 0.5 * (rates_arr[:-1] + rates_arr[1:]) * dt * 365.0
    cum = np.concatenate(([0.0], np.cumsum(step_area)))
    eur = compute_total_eur(
        model_type=req.model_type,
        params=req.params,
        horizon_years=req.horizon_years,
        economic_limit=req.economic_limit,
    )

    # Method-1 displayed EUR for the modal's stat row. Requires both
    # api10 and stream so we can resolve the well's first-prod anchor
    # and the per-stream actual cum. When either is omitted (legacy
    # callers), the response carries only ``eur`` and the modal
    # falls back to that.
    eur_displayed: float | None = None
    eur_remaining: float | None = None
    actual_cum_val: float | None = None
    if req.api10 and req.stream:
        well = session.get(Well, req.api10)
        vol_attr = _STREAM_VOL_ATTR[req.stream]
        agg = session.execute(
            select(
                func.sum(getattr(ProductionMonthly, vol_attr)),
                func.max(ProductionMonthly.prod_date),
            ).where(ProductionMonthly.api10 == req.api10)
        ).one()
        actual_cum_val = float(agg[0]) if agg[0] is not None else None
        last_prod_date: date | None = agg[1]
        # Anchor: with ramp params in the previewed param set, model
        # t=0 is at the stream's onset (first_prod + onset_index_months);
        # else at the saved forecast's peak. The editable param set the
        # modal posts doesn't carry onset (it's a fixed property of the
        # data), so fall back to the saved row's onset. Defaults to 0.
        has_ramp = req.params.get("peak_index_months") is not None and req.params.get("qo") is not None
        fc_row = session.execute(
            select(Forecast).where(
                Forecast.api10 == req.api10, Forecast.stream == req.stream
            )
        ).scalar_one_or_none()
        req_onset = req.params.get("onset_index_months")
        onset_m = int(
            req_onset
            if req_onset is not None
            else ((fc_row.params or {}).get("onset_index_months") or 0 if fc_row else 0)
        )
        anchor: date | None = None
        if has_ramp and well is not None and well.first_prod_date is not None:
            anchor = _add_months(well.first_prod_date, onset_m)
        else:
            # Legacy (no-ramp) previews anchor at the saved peak month.
            anchor = fc_row.peak_month_date if fc_row is not None else None
        if anchor is not None and last_prod_date is not None and last_prod_date >= anchor:
            t_years = _months_between(anchor, last_prod_date) / 12.0
            qi = req.params.get("qi"); Di = req.params.get("Di")
            b = req.params.get("b"); Df = req.params.get("Df")
            if qi is not None and Di is not None and b is not None and Df is not None:
                model_cum_endhist = model_cum_at_t(
                    qo=req.params.get("qo"),
                    peak_index_months=(
                        int(req.params["peak_index_months"])
                        if req.params.get("peak_index_months") is not None
                        else None
                    ),
                    qi=float(qi), Di=float(Di), b=float(b), Df=float(Df),
                    t_years=t_years,
                )
                eur_remaining = max(0.0, eur - model_cum_endhist)
                eur_displayed = float(actual_cum_val or 0.0) + eur_remaining
        if eur_displayed is None:
            # Insufficient anchor / params → fall back to model EUR.
            eur_remaining = eur
            eur_displayed = eur

    return PreviewResponse(
        t_years=t.tolist(),
        rate=rates_arr.tolist(),
        cum=cum.tolist(),
        eur=eur,
        eur_displayed=eur_displayed,
        eur_remaining=eur_remaining,
        actual_cum=actual_cum_val,
    )
