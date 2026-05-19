"""Forecast API.

  POST /api/forecasts/batch
      body: {api14s: [...], config?: {...}}
      → 202; runs in background; poll /api/sync/status for the SyncJob row.

  GET  /api/forecasts?api14=...
      → list per-stream forecasts for one or more wells.

  GET  /api/forecasts/{id}
      → single forecast.

  PATCH /api/forecasts/{id}
      body: {params?: {...}, locked?: bool, manual_override?: bool}
      → mutate fit params; recompute EUR; mark `manual_override=True`.

  GET  /api/forecasts/{api14}/curves
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
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
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
from app.forecasting.eur import compute_eur
from app.forecasting.fit import (
    STREAM_RATE_COLUMN,
    STREAM_VOLUME_COLUMN,
    detect_at_bound,
)
from app.forecasting.metrics import effective_decline_first_year
from app.forecasting.models import (
    arps_exponential,
    arps_harmonic,
    arps_hyperbolic,
    modified_hyperbolic,
)
from app.forecasting.orchestrator import forecast_wells
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
    min_post_peak_months: int = Field(default=6, ge=1, le=24)

    def to_config(self) -> ForecastConfig:
        return ForecastConfig(
            model_type=self.model_type,
            fit_method=self.fit_method,
            df_terminal_per_year=self.df_terminal_per_year,
            horizon_years=self.horizon_years,
            economic_limit_bopd=self.economic_limit_bopd,
            economic_limit_mcfd=self.economic_limit_mcfd,
            economic_limit_bwpd=self.economic_limit_bwpd,
            min_post_peak_months=self.min_post_peak_months,
        )


class BatchRequest(BaseModel):
    api14s: list[str] = Field(min_length=1, max_length=500)
    config: ForecastConfigBody | None = None


class BatchResponse(BaseModel):
    accepted: bool
    job_id: uuid.UUID
    well_count: int


class ForecastRow(BaseModel):
    id: uuid.UUID
    api14: str
    stream: Stream
    model_type: ModelType
    params: dict[str, Any]
    qi: float | None
    di_initial: float | None
    di_effective: float | None  # derived; first-year fraction (0–1)
    b: float | None
    df_terminal: float | None
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
    well_county: str | None = None

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
            id=f.id, api14=f.api14, stream=f.stream, model_type=f.model_type,
            params=f.params, qi=f.qi, di_initial=f.di_initial,
            di_effective=di_eff, b=f.b,
            df_terminal=f.df_terminal, eur=f.eur,
            peak_month_date=f.peak_month_date, peak_rate=f.peak_rate,
            fit_method=f.fit_method, fit_r2=f.fit_r2, fit_rmse=f.fit_rmse,
            fit_at_bound=at_bound, bound_note=bound_note,
            downtime_ratio=f.downtime_ratio,
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
    history_prodday_rate: list[float | None]   # rate_prodday_* (modal toggle)
    history_cum: list[float | None]
    forecast_months: list[date]         # extended into the future
    forecast_rate: list[float]
    forecast_cum: list[float]


class WellCurvesResponse(BaseModel):
    api14: str
    streams: list[StreamCurves]


class PreviewRequest(BaseModel):
    model_type: str = "modified_hyperbolic"
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


def _batch_bg(api14s: list[str], cfg: ForecastConfig, job_id: uuid.UUID) -> None:
    with SessionLocal() as session:
        job = session.get(SyncJob, job_id)
        if job is None:
            return
        job.status = SyncJobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        session.commit()
        try:
            forecast_wells(session, api14s, config=cfg)
            job.items_seen = len(api14s)
            job.items_upserted = len(api14s)
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
    # Use the existing sync_jobs table for batch-job tracking. SyncEntity
    # doesn't have a "forecast" value yet; "well_headers" is the closest
    # bucket but we shouldn't pretend that's what this is. Tag it via metadata.
    with SessionLocal() as session:
        session.add(SyncJob(
            id=job_id,
            entity=SyncEntity.WELL_HEADERS,
            scope_key=f"forecast:{len(req.api14s)}_wells",
            status=SyncJobStatus.PENDING,
            metadata_={"kind": "forecast_batch", "api14_count": len(req.api14s)},
        ))
        session.commit()
    background.add_task(_batch_bg, list(req.api14s), cfg, job_id)
    return BatchResponse(accepted=True, job_id=job_id, well_count=len(req.api14s))


# ============================ list / detail / patch ============================


@router.get("", response_model=list[ForecastRow])
def list_forecasts(
    api14: list[str] | None = Query(default=None),
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
            Well.county,
        )
        .join(Well, Well.api14 == Forecast.api14)
        .order_by(Forecast.api14, Forecast.stream)
    )
    if api14:
        stmt = stmt.where(Forecast.api14.in_(api14))
    out: list[ForecastRow] = []
    for f, name, operator, formation, lateral_ft, vintage_year, county in session.execute(stmt).all():
        row = ForecastRow.from_orm_row(f)
        # mutate in-place — model_copy(update=) would also work but allocates.
        row.well_name = name
        row.well_operator = operator
        row.well_formation = formation
        row.well_lateral_ft = float(lateral_ft) if lateral_ft is not None else None
        row.well_vintage_year = int(vintage_year) if vintage_year is not None else None
        row.well_county = county
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
    well = session.get(Well, f.api14)
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
        row.well_county = well.county
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
        # Recompute EUR with the edited params.
        econ_limit = req.economic_limit or DEFAULT_ECONOMIC_LIMIT_BOPD
        f.eur = compute_eur(
            f.model_type.value, merged, economic_limit=econ_limit
        )
        f.manual_override = True  # user-touched

    if req.locked is not None:
        f.locked = req.locked
    if req.manual_override is not None:
        f.manual_override = req.manual_override

    session.commit()
    return _row_with_well_join(f, session)


# ============================ curves (for detail modal) ============================


def _evaluate_rate(model_type: str, params: dict[str, float], t: np.ndarray) -> np.ndarray:
    if model_type == "arps_exponential":
        return arps_exponential(t, params["qi"], params["Di"])
    if model_type == "arps_harmonic":
        return arps_harmonic(t, params["qi"], params["Di"])
    if model_type == "arps_hyperbolic":
        return arps_hyperbolic(t, params["qi"], params["Di"], params["b"])
    if model_type == "modified_hyperbolic":
        return modified_hyperbolic(
            t, params["qi"], params["Di"], params["b"], params["Df"]
        )
    raise HTTPException(status_code=400, detail=f"unsupported model: {model_type}")


@router.get("/{api14}/curves", response_model=WellCurvesResponse)
def well_curves(
    api14: str,
    horizon_years: float = Query(default=DEFAULT_FORECAST_HORIZON_YEARS, gt=0),
    session: Session = Depends(get_session),
) -> WellCurvesResponse:
    """History (months 0..N) + forecast (t=0 at peak month → horizon) per stream."""
    prod_rows = session.execute(
        select(ProductionMonthly).where(ProductionMonthly.api14 == api14)
        .order_by(ProductionMonthly.prod_date)
    ).scalars().all()
    if not prod_rows:
        raise HTTPException(status_code=404, detail=f"no production for {api14}")

    forecasts = session.execute(
        select(Forecast).where(Forecast.api14 == api14)
    ).scalars().all()
    fc_by_stream = {f.stream: f for f in forecasts}

    streams: list[StreamCurves] = []
    for stream_name in ("oil", "gas", "water"):
        st = Stream(stream_name)
        rate_col = STREAM_RATE_COLUMN[stream_name]
        vol_col = STREAM_VOLUME_COLUMN[stream_name]
        prodday_col = rate_col.replace("rate_calday", "rate_prodday")

        months = [r.prod_date for r in prod_rows]
        history_rate = [getattr(r, rate_col) for r in prod_rows]
        history_prodday = [getattr(r, prodday_col) for r in prod_rows]
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
            # Evaluate from peak forward in monthly resolution.
            # Index 0 is the peak month (t=0), so rate[0] = qi exactly and
            # the cum starts at 0 — matching the preview endpoint, which
            # also anchors its first sample at t≈0. Before this change,
            # curves evaluated at t = (i+1)/12 and integrated as
            # `cum_running += rate * 30.4375`, which left cum[0] at a
            # full month's worth of production (~3000+ BBL for a typical
            # Permian oil well) and made the cum chart visibly snap up
            # whenever the live preview overlay cleared after Save Override.
            n_pts = int(horizon_years * 12)
            t = np.arange(n_pts, dtype=float) / 12.0
            r = _evaluate_rate(fc.model_type.value, fc.params, t)
            cum_running = 0.0
            for i in range(n_pts):
                rate_i = float(r[i])
                forecast_rate.append(rate_i)
                # Trapezoid step from t[i-1] to t[i] (one month apart).
                # cum[0] = 0 (no production has accumulated AT the peak
                # boundary itself). Each subsequent step adds the
                # average of the two end-rates times the 30.4375-day
                # month length.
                if i > 0:
                    cum_running += 0.5 * (float(r[i - 1]) + rate_i) * 30.4375
                forecast_cum.append(cum_running)
                # Walk months forward from peak_month_date. Index 0
                # corresponds to peak_month itself (not peak+1).
                yr = fc.peak_month_date.year + (fc.peak_month_date.month - 1 + i) // 12
                mo = ((fc.peak_month_date.month - 1 + i) % 12) + 1
                forecast_months.append(date(yr, mo, 1))

        streams.append(StreamCurves(
            stream=st,
            months=months,
            history_rate=[float(v) if v is not None else None for v in history_rate],
            history_prodday_rate=[float(v) if v is not None else None for v in history_prodday],
            history_cum=[float(v) for v in history_cum],
            forecast_months=forecast_months,
            forecast_rate=forecast_rate,
            forecast_cum=forecast_cum,
        ))

    return WellCurvesResponse(api14=api14, streams=streams)


# ============================ preview (live re-render) ============================


@router.post("/preview", response_model=PreviewResponse)
def forecast_preview(req: PreviewRequest) -> PreviewResponse:
    # Sample from t=0 (peak boundary, rate=qi) through the horizon so
    # rate[0] sits exactly at the peak and cum[0] is 0 — same convention
    # as /api/forecasts/{api14}/curves. The previous version sampled
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
    eur = compute_eur(
        req.model_type, req.params,
        horizon_years=req.horizon_years,
        economic_limit=req.economic_limit,
    )
    return PreviewResponse(
        t_years=t.tolist(),
        rate=rates_arr.tolist(),
        cum=cum.tolist(),
        eur=eur,
    )
