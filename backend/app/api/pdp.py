"""PDP forecasting from seller data-room daily production.

  GET    /api/vdr/sources                               data rooms in the warehouse
  GET    /api/deals/{id}/pdp/config                     the deal's pdp_config
  PUT    /api/deals/{id}/pdp/config                     set vdr_id / api10s / uptime_overrides
  POST   /api/deals/{id}/pdp/sync                       copy the data room's daily rows locally
  POST   /api/deals/{id}/pdp/forecast                   fit / transfer every (or listed) well
  GET    /api/deals/{id}/pdp/forecasts                  persisted rows + review flags
  GET    /api/deals/{id}/pdp/forecasts/{api10}/{stream}/series   chart payload
  PATCH  /api/deals/{id}/pdp/forecasts/{api10}/{stream} fit window, manual params, lock
  PUT    /api/deals/{id}/pdp/wells/{api10}/uptime       per-well uptime override (+ re-run)
  POST   /api/deals/{id}/pdp/lock                       lock / unlock every stream of listed wells
  PUT    /api/deals/{id}/pdp/export-config              effective date / grouping / curve months
  GET    /api/deals/{id}/pdp/export/preview             group totals + readiness warnings
  GET    /api/deals/{id}/pdp/export.xlsx                Blue Ox PDP workbook (contract §2)

Method of record: app.forecasting.daily. Rows live in ``pdp_forecasts``,
never the global ``forecasts`` table.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Deal, PdpForecast, Stream, VdrDailyProduction, Well
from app.db.session import get_session
from app.exports.blueox import XLSX_MEDIA_TYPE, BlueOxContractError
from app.exports.blueox_pdp import build_pdp_workbook, pdp_filename
from app.pdp import export as pdp_export
from app.pdp import service
from app.warehouse_client.session import get_warehouse_session
from app.warehouse_client.vdr import fetch_vdr_sources

router = APIRouter(tags=["pdp"])

StreamName = Literal["oil", "gas", "water"]


class PdpConfigBody(BaseModel):
    vdr_id: str = Field(pattern=r"^[a-z0-9_]+$")
    api10s: list[str] | None = None
    uptime_overrides: dict[str, float] = Field(default_factory=dict)
    # Seller 2PDNP wells convey too (zero forecast unless a restart is set).
    include_pdnp: bool = False


class ForecastRequest(BaseModel):
    api10s: list[str] | None = None


class ManualParams(BaseModel):
    qi: float = Field(gt=0)
    Di: float = Field(gt=0)
    b: float = Field(ge=0)
    anchor_date: date


class PatchRequest(BaseModel):
    # Present-and-null clears the window; absent leaves it alone.
    fit_start_date: date | None = None
    clear_fit_start: bool = False
    params: ManualParams | None = None
    locked: bool | None = None


def _deal(session: Session, deal_id: uuid.UUID) -> Deal:
    deal = session.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(404, "deal not found")
    return deal


def _cfg(deal: Deal) -> service.PdpConfig:
    try:
        return service.PdpConfig.from_deal(deal)
    except service.PdpConfigError as e:
        raise HTTPException(409, str(e)) from e


def _row(r: PdpForecast) -> dict[str, Any]:
    return {
        "api10": r.api10,
        "stream": r.stream.value,
        "method": r.method,
        "qi": r.qi,
        "Di": r.di_initial,
        "b": r.b,
        "Df": r.df_terminal,
        "anchor_date": r.anchor_date,
        "fit_start_date": r.fit_start_date,
        "data_through": r.data_through,
        "uptime_factor": r.uptime_factor,
        "uptime_basis": r.uptime_basis,
        "fit_r2_log": r.fit_r2_log,
        "n_points": r.n_points,
        "tail_ratio": r.tail_ratio,
        "cum_to_date": r.cum_to_date,
        "remaining": r.remaining,
        "eur": r.eur,
        "review_flags": r.review_flags,
        "breaks": r.breaks,
        "diagnostics": r.diagnostics,
        "manual_override": r.manual_override,
        "locked": r.locked,
        "updated_at": r.updated_at,
    }


def _outcomes(results: dict[str, list[service.StreamOutcome]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    per_well: dict[str, dict[str, str]] = {}
    for api10, outs in results.items():
        per_well[api10] = {}
        for o in outs:
            counts[o.status] = counts.get(o.status, 0) + 1
            per_well[api10][o.stream] = o.status if o.detail is None else f"{o.status}: {o.detail}"
    return {"counts": counts, "wells": per_well}


@router.get("/vdr/sources")
def list_vdr_sources() -> list[dict[str, Any]]:
    with contextmanager(get_warehouse_session)() as wh:
        return [s.__dict__ for s in fetch_vdr_sources(wh)]


@router.get("/deals/{deal_id}/pdp/config")
def get_pdp_config(
    deal_id: uuid.UUID, session: Session = Depends(get_session)
) -> dict[str, Any] | None:
    return _deal(session, deal_id).pdp_config


@router.put("/deals/{deal_id}/pdp/config")
def put_pdp_config(
    deal_id: uuid.UUID, body: PdpConfigBody, session: Session = Depends(get_session)
) -> dict[str, Any]:
    deal = _deal(session, deal_id)
    bad = {k: v for k, v in body.uptime_overrides.items() if not 0 < v <= 1}
    if bad:
        raise HTTPException(422, f"uptime overrides must be in (0, 1]: {bad}")
    # MERGE, never replace: a data-room save must not wipe the per-well
    # uptime overrides or the export settings stored alongside it. Only
    # fields the client actually sent are written.
    deal.pdp_config = {**(deal.pdp_config or {}), **body.model_dump(exclude_unset=True)}
    session.commit()
    return deal.pdp_config


@router.post("/deals/{deal_id}/pdp/sync")
def sync_pdp(deal_id: uuid.UUID, session: Session = Depends(get_session)) -> dict[str, Any]:
    cfg = _cfg(_deal(session, deal_id))
    with contextmanager(get_warehouse_session)() as wh:
        try:
            return service.sync_vdr_daily(session, wh, cfg)
        except service.PdpConfigError as e:
            raise HTTPException(409, str(e)) from e


@router.post("/deals/{deal_id}/pdp/forecast")
def run_pdp_forecast(
    deal_id: uuid.UUID, req: ForecastRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    deal = _deal(session, deal_id)
    try:
        return _outcomes(service.forecast_deal(session, deal, _cfg(deal), req.api10s))
    except service.PdpConfigError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/deals/{deal_id}/pdp/forecasts")
def list_pdp_forecasts(
    deal_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[dict[str, Any]]:
    _deal(session, deal_id)
    rows = list(
        session.execute(
            select(PdpForecast)
            .where(PdpForecast.deal_id == deal_id)
            .order_by(PdpForecast.api10, PdpForecast.stream)
        ).scalars()
    )
    api10s = sorted({r.api10 for r in rows})
    names = dict(
        session.execute(
            select(VdrDailyProduction.api10, VdrDailyProduction.well_name)
            .where(VdrDailyProduction.api10.in_(api10s))
            .distinct()
        ).all()
    )
    benches = dict(
        session.execute(
            select(Well.api10, Well.formation_blueox).where(Well.api10.in_(api10s))
        ).all()
    )
    return [
        {**_row(r), "well_name": names.get(r.api10), "formation_blueox": benches.get(r.api10)}
        for r in rows
    ]


@router.get("/deals/{deal_id}/pdp/forecasts/{api10}/{stream}/series")
def pdp_series(
    deal_id: uuid.UUID,
    api10: str,
    stream: StreamName,
    years_ahead: float = Query(5.0, gt=0, le=50),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    cfg = _cfg(_deal(session, deal_id))
    row = session.execute(
        select(PdpForecast).where(
            PdpForecast.deal_id == deal_id,
            PdpForecast.api10 == api10,
            PdpForecast.stream == Stream(stream),
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"no PDP forecast for {api10}/{stream}")
    return service.stream_series(session, cfg, row, years_ahead)


class UptimeBody(BaseModel):
    # null clears the override (back to the computed routine-downtime factor)
    uptime: float | None = Field(default=None, gt=0, le=1)


@router.put("/deals/{deal_id}/pdp/wells/{api10}/uptime")
def put_well_uptime(
    deal_id: uuid.UUID, api10: str, body: UptimeBody, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Per-well uptime override; re-runs that well (windows, manual params
    and locks are all preserved — volumes pick up the new factor)."""
    deal = _deal(session, deal_id)
    cfg_json = dict(deal.pdp_config or {})
    overrides = dict(cfg_json.get("uptime_overrides") or {})
    if body.uptime is None:
        overrides.pop(api10, None)
    else:
        overrides[api10] = body.uptime
    cfg_json["uptime_overrides"] = overrides
    deal.pdp_config = cfg_json
    session.commit()
    return _outcomes({api10: service.forecast_well(session, deal, _cfg(deal), api10)})


@router.patch("/deals/{deal_id}/pdp/forecasts/{api10}/{stream}")
def patch_pdp_forecast(
    deal_id: uuid.UUID,
    api10: str,
    stream: StreamName,
    req: PatchRequest,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    deal = _deal(session, deal_id)
    cfg = _cfg(deal)

    def load() -> PdpForecast:
        row = session.execute(
            select(PdpForecast).where(
                PdpForecast.deal_id == deal_id,
                PdpForecast.api10 == api10,
                PdpForecast.stream == Stream(stream),
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, f"no PDP forecast for {api10}/{stream}")
        return row

    row = load()
    edits = req.params is not None or req.fit_start_date is not None or req.clear_fit_start
    if row.locked and edits and req.locked is not False:
        raise HTTPException(409, f"{api10}/{stream} is locked — unlock first")
    if req.locked is False:
        row.locked = False
        session.commit()
    if req.params is not None and (req.fit_start_date is not None or req.clear_fit_start):
        raise HTTPException(422, "set either params or a fit window, not both")
    if req.fit_start_date is not None or req.clear_fit_start:
        outs = service.forecast_well(
            session,
            deal,
            cfg,
            api10,
            streams=(stream,),
            fit_start={stream: None if req.clear_fit_start else req.fit_start_date},
        )
        if outs[0].status not in ("fitted", "transferred"):
            raise HTTPException(
                422, f"refit {api10}/{stream}: {outs[0].status} {outs[0].detail or ''}".strip()
            )
    elif req.params is not None:
        p = req.params
        service.set_manual_params(
            session, deal, cfg, load(), qi=p.qi, di=p.Di, b=p.b, anchor_date=p.anchor_date
        )
    if req.locked is True:
        row = load()
        row.locked = True
        session.commit()
    session.expire_all()
    return _row(load())


class ExportConfigBody(BaseModel):
    effective_date: date
    grouping: Literal["well", "lease", "custom"] = "well"
    groups: dict[str, list[str]] = Field(default_factory=dict)
    curve_months: int | None = Field(default=None, gt=0, le=1200)
    # Re-export: the prior governing filename this file replaces (§2 Principle 1/5).
    supersedes: str | None = None


@router.put("/deals/{deal_id}/pdp/export-config")
def put_export_config(
    deal_id: uuid.UUID, body: ExportConfigBody, session: Session = Depends(get_session)
) -> dict[str, Any]:
    deal = _deal(session, deal_id)
    cfg_json = dict(deal.pdp_config or {})
    cfg_json["export"] = body.model_dump(mode="json")
    deal.pdp_config = cfg_json
    session.commit()
    return cfg_json["export"]


def _assemble(session: Session, deal_id: uuid.UUID) -> Any:
    deal = _deal(session, deal_id)
    cfg = _cfg(deal)
    try:
        return pdp_export.assemble(session, deal, cfg, pdp_export.ExportConfig.from_deal(deal))
    except pdp_export.PdpExportError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/deals/{deal_id}/pdp/export/preview")
def preview_pdp_export(
    deal_id: uuid.UUID, session: Session = Depends(get_session)
) -> dict[str, Any]:
    data = _assemble(session, deal_id)
    out = pdp_export.preview(data)
    try:
        build_pdp_workbook(data)
        out["contract_errors"] = None
    except BlueOxContractError as e:
        out["contract_errors"] = str(e)
    return out


@router.get("/deals/{deal_id}/pdp/export.xlsx")
def download_pdp_export(deal_id: uuid.UUID, session: Session = Depends(get_session)) -> Response:
    data = _assemble(session, deal_id)
    try:
        body = build_pdp_workbook(data)
    except BlueOxContractError as e:
        raise HTTPException(422, str(e)) from e
    name = pdp_filename(data.codename, data.export_date)
    return Response(
        content=body,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


class LockRequest(BaseModel):
    api10s: list[str] = Field(min_length=1)
    locked: bool = True


@router.post("/deals/{deal_id}/pdp/lock")
def lock_wells(
    deal_id: uuid.UUID, req: LockRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    """Well-level sign-off: (un)lock every stream row of the listed wells.
    Parameters are untouched — locking only shields rows from re-runs."""
    _deal(session, deal_id)
    rows = list(
        session.execute(
            select(PdpForecast).where(
                PdpForecast.deal_id == deal_id, PdpForecast.api10.in_(req.api10s)
            )
        ).scalars()
    )
    missing = sorted(set(req.api10s) - {r.api10 for r in rows})
    if missing:
        raise HTTPException(404, f"no PDP forecasts for {missing}")
    changed = 0
    for r in rows:
        if r.locked != req.locked:
            r.locked = req.locked
            changed += 1
    session.commit()
    return {
        "wells": len(req.api10s),
        "streams": len(rows),
        "changed": changed,
        "locked": req.locked,
    }
