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
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    FitMethod,
    Forecast,
    ModelType,
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
from app.forecasting.eur import compute_eur
from app.forecasting.fit import (
    STREAM_RATE_COLUMN,
    STREAM_VOLUME_COLUMN,
    _flag_downtime,
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
    forecast_wells,
    stream_rate_at_first_prod,
    stream_rate_at_peak,
)
from app.forecasting.peak_detection import detect_oil_peak
from app.forecasting.ramp_arps import compute_total_eur, evaluate_well_rate
from app.forecasting.types import (
    DEFAULT_ECONOMIC_LIMIT_BOPD,
    DEFAULT_FORECAST_HORIZON_YEARS,
    ForecastConfig,
)

router = APIRouter(prefix="/forecasts", tags=["forecasts"])
log = get_logger("api.forecasts")

# ============================ pydantic schemas ============================


class ForecastConfigBody(BaseModel):
    model_type: str = "modified_hyperbolic"
    fit_method: str = "rate_cum"
    df_terminal_per_year: float = Field(default=0.08, ge=0.0, le=0.5)
    horizon_years: float = Field(default=DEFAULT_FORECAST_HORIZON_YEARS, gt=0.0, le=100.0)
    economic_limit_bopd: float = Field(default=DEFAULT_ECONOMIC_LIMIT_BOPD, ge=0.0)
    economic_limit_mcfd: float = Field(default=30.0, ge=0.0)
    economic_limit_bwpd: float = Field(default=50.0, ge=0.0)
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

    @classmethod
    def from_orm_row(cls, f: Forecast) -> "ForecastRow":
        di_eff = (
            effective_decline_first_year(f.di_initial, f.b)
            if f.di_initial is not None
            else None
        )
        at_bound, bound_note = (False, None)
        if f.qi is not None and f.di_initial is not None and f.peak_rate is not None:
            at_bound, bound_note = detect_at_bound(
                qi=f.qi, di=f.di_initial, b=f.b, peak_rate=f.peak_rate
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


class PreviewResponse(BaseModel):
    t_years: list[float]
    rate: list[float]
    cum: list[float]
    eur: float


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
    # between consecutive non-null samples. ~30.4375 days per month
    # matches the convention everywhere else in this file.
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
            running += 0.5 * (last_rate + ri) * 30.4375 * (i - last_idx)
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
    the oil-peak month) plus the cohort medians. Honors `locked` on
    existing short-side rows — a locked row is preserved.

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
        peak = detect_oil_peak(monthly)
        if peak is None:
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

            qi = stream_rate_at_peak(monthly, peak, stream_str)
            # Ramp prefix is well-specific even on a cohort transfer:
            # qo comes from the short well's own first-prod observation
            # and peak_index_months from its own detected peak. Only
            # Di/b are inherited from the long-history cohort median.
            qo = stream_rate_at_first_prod(monthly, stream_str)
            peak_index_months = int(peak.peak_index) if peak.peak_index > 0 else None
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
                "peak_month_date": peak.peak_month_date,
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
            update_cols = {
                c: stmt.excluded[c]
                for c in values
                if c not in {
                    "id", "api10", "stream", "created_at",
                    "manual_override", "locked",
                }
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


@router.get("", response_model=list[ForecastRow])
def list_forecasts(
    api10: list[str] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ForecastRow]:
    """List forecasts joined to wells for the review/forecast grids.

    Single-trip JOIN avoids the N+1 well-lookup the frontend used to do.
    """
    stmt = (
        select(
            Forecast,
            Well.name,
            Well.operator,
            Well.formation,
            Well.lateral_ft,
            Well.vintage_year,
            Well.first_prod_date,
            Well.county,
            Well.novi_oil_eur,
        )
        .join(Well, Well.api10 == Forecast.api10)
        .order_by(Forecast.api10, Forecast.stream)
    )
    if api10:
        stmt = stmt.where(Forecast.api10.in_(api10))
    out: list[ForecastRow] = []
    for (
        f, name, operator, formation, lateral_ft, vintage_year,
        first_prod_date, county, novi_oil_eur,
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
        row.well_formation = well.formation
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
        if fc and fc.peak_month_date is not None:
            n_pts = int(horizon_years * 12)
            t = np.arange(n_pts, dtype=float) / 12.0
            # Anchor at first_prod when the row has ramp params (the
            # post-migration / re-fit case); peak_month otherwise for
            # back-compat with rows that pre-date the ramp columns.
            has_ramp = (
                fc.peak_index_months is not None
                or fc.params.get("peak_index_months") is not None
            )
            anchor_date = first_prod_date if has_ramp else fc.peak_month_date
            r = _evaluate_rate(fc.model_type.value, fc.params, t)
            cum_running = 0.0
            for i in range(n_pts):
                rate_i = float(r[i])
                forecast_rate.append(rate_i)
                # Trapezoid step from t[i-1] to t[i] (one month apart).
                # cum[0] = 0 (no production has accumulated AT the
                # anchor boundary itself). Each subsequent step adds
                # the average of the two end-rates times the
                # 30.4375-day month length.
                if i > 0:
                    cum_running += 0.5 * (float(r[i - 1]) + rate_i) * 30.4375
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
        ))

    return WellCurvesResponse(api10=api10, streams=streams)


# ============================ preview (live re-render) ============================


@router.post("/preview", response_model=PreviewResponse)
def forecast_preview(req: PreviewRequest) -> PreviewResponse:
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
    return PreviewResponse(
        t_years=t.tolist(),
        rate=rates_arr.tolist(),
        cum=cum.tolist(),
        eur=eur,
    )
