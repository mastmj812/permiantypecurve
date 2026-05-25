"""Deals API + deal-level Excel export.

A deal groups type curves for a single acquisition / divestiture so an
engineer can hand off all of them as one workbook (one metadata sheet +
one fitted-forecast sheet per curve). Cardinality is 1:N — a curve has
at most one deal via ``type_curves.deal_id``.

  POST   /api/deals                        create
  GET    /api/deals                        list with curve counts
  GET    /api/deals/{id}                   detail incl. assigned curve summaries
  PATCH  /api/deals/{id}                   rename / notes
  DELETE /api/deals/{id}                   delete (un-assigns curves via FK SET NULL)
  GET    /api/deals/{id}/export.xlsx       Excel workbook bundling the deal
"""

from __future__ import annotations

import io
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.type_curves import (
    TypeCurveSummary,
    _FITTED_PARAM_KEYS,
    _FORECAST_N_MONTHS,
    _PERCENTILE_KEYS_WITH_MEAN,
    _DAYS_PER_MONTH,
    _evaluate_fitted_rates,
    _fitted_eur_per_1000ft,
    _fitted_p50_params,
)
from app.core.logging import get_logger
from app.db.models import Deal, TypeCurve
from app.db.session import get_session
from app.exports.well_rows import (
    PER_WELL_COL_FORMATS,
    PER_WELL_HEADERS,
    per_well_rows,
)
from app.type_curves.aggregate import PERCENTILE_KEYS

router = APIRouter(prefix="/deals", tags=["deals"])
log = get_logger("api.deals")

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


# ============================ schemas ============================


class DealCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    notes: str | None = None


class DealPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    notes: str | None = None


class DealSummary(BaseModel):
    id: uuid.UUID
    name: str
    notes: str | None
    created_at: datetime
    n_curves: int


class DealRow(BaseModel):
    id: uuid.UUID
    name: str
    notes: str | None
    created_at: datetime
    curves: list[TypeCurveSummary]


# ============================ helpers ============================


def _curve_summary(tc: TypeCurve) -> TypeCurveSummary:
    return TypeCurveSummary(
        id=tc.id,
        name=tc.name,
        notes=tc.notes,
        normalization_basis=tc.normalization_basis,
        alignment_method=tc.alignment_method,
        n_wells=len(tc.included_api14s or []),
        created_at=tc.created_at,
        version_of=tc.version_of,
        deal_id=tc.deal_id,
    )


def _load_or_404(session: Session, deal_id: uuid.UUID) -> Deal:
    deal = session.get(Deal, deal_id)
    if deal is None:
        raise HTTPException(status_code=404, detail="deal not found")
    return deal


# ============================ CRUD ============================


@router.post("", response_model=DealRow, status_code=201)
def create_deal(req: DealCreate, session: Session = Depends(get_session)) -> DealRow:
    deal = Deal(id=uuid.uuid4(), name=req.name, notes=req.notes)
    session.add(deal)
    try:
        session.commit()
    except IntegrityError:
        # Unique constraint on name — surface as 409 so the frontend can
        # show a friendly "deal already exists" message.
        session.rollback()
        raise HTTPException(status_code=409, detail="deal name already exists") from None
    session.refresh(deal)
    log.info("deal_created", id=str(deal.id), name=deal.name)
    return DealRow(
        id=deal.id,
        name=deal.name,
        notes=deal.notes,
        created_at=deal.created_at,
        curves=[],
    )


@router.get("", response_model=list[DealSummary])
def list_deals(session: Session = Depends(get_session)) -> list[DealSummary]:
    # Single query: join deals to a per-deal curve-count subquery so the
    # list endpoint stays O(1) regardless of how many curves exist.
    count_subq = (
        select(TypeCurve.deal_id, func.count().label("n"))
        .where(TypeCurve.deal_id.is_not(None))
        .group_by(TypeCurve.deal_id)
        .subquery()
    )
    rows = session.execute(
        select(Deal, func.coalesce(count_subq.c.n, 0))
        .outerjoin(count_subq, count_subq.c.deal_id == Deal.id)
        .order_by(Deal.created_at.desc())
    ).all()
    return [
        DealSummary(
            id=d.id,
            name=d.name,
            notes=d.notes,
            created_at=d.created_at,
            n_curves=int(n),
        )
        for d, n in rows
    ]


@router.get("/{deal_id}", response_model=DealRow)
def get_deal(deal_id: uuid.UUID, session: Session = Depends(get_session)) -> DealRow:
    deal = _load_or_404(session, deal_id)
    curves = session.execute(
        select(TypeCurve)
        .where(TypeCurve.deal_id == deal_id)
        .order_by(TypeCurve.name)
    ).scalars().all()
    return DealRow(
        id=deal.id,
        name=deal.name,
        notes=deal.notes,
        created_at=deal.created_at,
        curves=[_curve_summary(c) for c in curves],
    )


@router.patch("/{deal_id}", response_model=DealRow)
def patch_deal(
    deal_id: uuid.UUID,
    req: DealPatch,
    session: Session = Depends(get_session),
) -> DealRow:
    deal = _load_or_404(session, deal_id)
    if req.name is not None:
        deal.name = req.name
    if "notes" in req.model_fields_set:
        # Allow clearing notes via explicit null — symmetric with the
        # deal_id un-assign behavior on type-curves PATCH.
        deal.notes = req.notes
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="deal name already exists") from None
    session.refresh(deal)
    return get_deal(deal_id, session)


@router.delete("/{deal_id}", status_code=204)
def delete_deal(deal_id: uuid.UUID, session: Session = Depends(get_session)) -> Response:
    deal = _load_or_404(session, deal_id)
    session.delete(deal)
    session.commit()
    # FK is ON DELETE SET NULL, so any assigned curves now have deal_id=null
    # rather than being cascade-deleted.
    log.info("deal_deleted", id=str(deal_id))
    return Response(status_code=204)


# ============================ Excel export ============================


# Excel caps sheet names at 31 chars and forbids \ / ? * [ ] :.
# We reserve room for the " — meta" / " — forecast" suffix (longest is
# 11 chars after sanitization, since the en-dash is one char). 22 keeps
# us inside the cap with a margin.
_SHEET_SLUG_MAX = 22
_SHEET_FORBIDDEN_RE = re.compile(r"[\\/?*\[\]:]+")


def _sheet_slug(name: str, used: set[str]) -> str:
    """Sanitize a curve name into a unique sheet-name prefix.

    Strips Excel-forbidden chars, truncates to ``_SHEET_SLUG_MAX``, and
    appends ``_2``, ``_3`` … on collision so two curves with names that
    truncate to the same prefix get distinct sheets.
    """
    base = _SHEET_FORBIDDEN_RE.sub("_", name).strip()
    base = base[:_SHEET_SLUG_MAX] or "curve"
    candidate = base
    n = 2
    while candidate in used:
        suffix = f"_{n}"
        candidate = f"{base[: _SHEET_SLUG_MAX - len(suffix)]}{suffix}"
        n += 1
    used.add(candidate)
    return candidate


# Humanize the normalization-basis enum for export labels. The DB enum
# value "per_lateral_ft" is historical — the actual aggregation math
# (aggregate.py::_normalizer) divides by lateral_ft / 1000, so rates and
# EUR are expressed PER 1,000 LATERAL FT. The chart axes and the EUR
# table header already say "1,000 ft"; this brings the export metadata
# sheet into line so the downstream economics tool isn't misled.
_NORMALIZATION_LABEL: dict[str, str] = {
    "per_lateral_ft": "per_1000_lateral_ft",
    "per_proppant_lb": "per_million_proppant_lb",
    "per_well": "per_well",
}


def _write_metadata_sheet(ws: Any, tc: TypeCurve, session: Session) -> None:
    """Two-column field/value layout mirroring ``_metadata_csv`` for the
    top section, then a wide per-well summary block at the bottom that
    expands the bare api14 list into completion-design + EUR metrics
    the engineer wants alongside the type-curve params. The CSV-zip
    export still ships just the api14 list — when an engineer needs
    the wider context they're already in xlsx territory."""
    from openpyxl.styles import Font

    bold = Font(bold=True)
    ws.append(["field", "value"])
    ws["A1"].font = bold
    ws["B1"].font = bold

    ws.append(["id", str(tc.id)])
    ws.append(["name", tc.name])
    ws.append(["notes", tc.notes or ""])
    ws.append([
        "normalization_basis",
        _NORMALIZATION_LABEL.get(
            tc.normalization_basis.value, tc.normalization_basis.value
        ),
    ])
    ws.append(["alignment_method", tc.alignment_method.value])
    ws.append(["created_at", tc.created_at.isoformat()])
    ws.append(["version_of", str(tc.version_of) if tc.version_of else ""])
    ws.append(["n_wells", len(tc.included_api14s or [])])
    ws.append([])

    ws.append(["filter_spec_key", "filter_spec_value"])
    last_row = ws.max_row
    ws.cell(row=last_row, column=1).font = bold
    ws.cell(row=last_row, column=2).font = bold
    for k, v in (tc.filter_spec or {}).items():
        # JSON-serializable scalars; lists/dicts get stringified so the
        # sheet stays in two-column shape regardless of value type.
        ws.append([k, v if isinstance(v, (str, int, float, bool)) else str(v)])
    ws.append([])

    # 50-yr Arps projection per percentile — the EUR the downstream
    # economics tool actually wants. Computed at export time from the
    # persisted fitted_per_percentile params (no econ cutoff; this tool
    # is technical-only). The previous block here reported the
    # data-window cumsum, which is a look-back QC artifact and was
    # easily mistaken for an EUR — dropped on purpose.
    ws.append(["fitted_eur_per_1000ft (50-yr Arps projection)"])
    ws.cell(row=ws.max_row, column=1).font = bold
    header = ["stream", *PERCENTILE_KEYS, "mean"]
    ws.append(header)
    for col_idx in range(1, len(header) + 1):
        ws.cell(row=ws.max_row, column=col_idx).font = bold
    streams = (tc.series or {}).get("streams", {})
    for s_name in ("oil", "gas", "water"):
        s = streams.get(s_name, {})
        eur = _fitted_eur_per_1000ft(s)
        ws.append(
            [s_name, *(eur.get(k) for k in PERCENTILE_KEYS), eur.get("mean")]
        )
    ws.append([])

    # Raw P50 Arps params per stream — same shape the
    # `app.type_curves.fit_p50.evaluate_fit` helper produces. Lets the
    # downstream econ tool re-evaluate the curve at its own time grid or
    # apply economic-limit cutoffs without re-fitting from rate columns.
    # Units: qi/qo in BOPD or MCFD per 1000 ft (matches the
    # normalization_basis row above); Di/Df in per-year nominal; b
    # dimensionless; peak_index in months.
    ws.append(["fitted_p50_params (per stream)"])
    ws.cell(row=ws.max_row, column=1).font = bold
    param_header = ["stream", *_FITTED_PARAM_KEYS]
    ws.append(param_header)
    for col_idx in range(1, len(param_header) + 1):
        ws.cell(row=ws.max_row, column=col_idx).font = bold
    for s_name in ("oil", "gas", "water"):
        s = streams.get(s_name, {})
        params = _fitted_p50_params(s)
        if params is None:
            ws.append([s_name, *[None for _ in _FITTED_PARAM_KEYS]])
            continue
        ws.append([s_name, *(params.get(k) for k in _FITTED_PARAM_KEYS)])
    ws.append([])

    # Per-well summary block — expands the bare api14 list into a wide
    # table with completion-design (BWPF / PPF) and per-well EUR
    # metrics. Engineers compare these against the cohort's published
    # fit params above to spot wells dragging the TC away from the
    # cohort average. EURs are recomputed via monthly trapezoid so the
    # workbook matches the Review tab + the slide-export probit.
    ws.append(["per_well_summary"])
    ws.cell(row=ws.max_row, column=1).font = bold
    ws.append(list(PER_WELL_HEADERS))
    header_row = ws.max_row
    for col_idx in range(1, len(PER_WELL_HEADERS) + 1):
        ws.cell(row=header_row, column=col_idx).font = bold
    for row in per_well_rows(session, list(tc.included_api14s or [])):
        ws.append(list(row))
        data_row = ws.max_row
        for col, fmt in PER_WELL_COL_FORMATS.items():
            ws.cell(row=data_row, column=col).number_format = fmt

    # Top metadata block is 2 columns; the per-well block extends out
    # to column 12. Size the first two for the metadata field/value
    # readability and let the per-well columns auto-size by content.
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 28
    # Wellname tends to be long ("SLINGSHOT A1 2760H"); give it room.
    ws.column_dimensions["C"].width = 26
    ws.column_dimensions["D"].width = 18
    for col_letter in ("E", "F", "G", "H", "I", "J", "K", "L"):
        ws.column_dimensions[col_letter].width = 14


def _write_forecast_sheet(ws: Any, tc: TypeCurve) -> None:
    """Wide layout: month index then ``{stream}_{pct}_{rate|cum}`` columns.

    Three streams × six percentiles × two value columns = 36 data columns
    plus the month column = 37. 600 monthly rows = the same 50-year
    horizon the CSV export uses. Empty cells for any percentile whose
    fit is missing (same fail-open semantic as ``_forecast_csv``)."""
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    streams = (tc.series or {}).get("streams", {})
    alignment_val = tc.alignment_method.value
    month_col = (
        "month_since_first_prod" if alignment_val == "first_prod_month" else "month_since_peak"
    )

    header: list[str] = [month_col]
    for s_name in ("oil", "gas", "water"):
        for key in _PERCENTILE_KEYS_WITH_MEAN:
            header.append(f"{s_name}_{key}_rate")
            header.append(f"{s_name}_{key}_cum")
    ws.append(header)
    bold = Font(bold=True)
    for col_idx in range(1, len(header) + 1):
        ws.cell(row=1, column=col_idx).font = bold

    # Evaluate once per stream, cache rate arrays + running cum counters.
    rates_by_stream: dict[str, dict[str, list[float] | None]] = {}
    cums_by_stream: dict[str, dict[str, float]] = {}
    for s_name in ("oil", "gas", "water"):
        s = streams.get(s_name)
        rates_by_stream[s_name] = _evaluate_fitted_rates(s or {})
        cums_by_stream[s_name] = {k: 0.0 for k in _PERCENTILE_KEYS_WITH_MEAN}

    for i in range(_FORECAST_N_MONTHS):
        row: list[Any] = [i + 1]
        for s_name in ("oil", "gas", "water"):
            rates_by_pct = rates_by_stream[s_name]
            cum_by_pct = cums_by_stream[s_name]
            for key in _PERCENTILE_KEYS_WITH_MEAN:
                arr = rates_by_pct.get(key)
                if arr is None or i >= len(arr):
                    row.append(None)
                    row.append(None)
                    continue
                rate = float(arr[i])
                cum_by_pct[key] += rate * _DAYS_PER_MONTH
                row.append(rate)
                row.append(cum_by_pct[key])
        ws.append(row)

    # Freeze the header row + month column so the column labels stay
    # visible while scrolling through 600 monthly rows.
    ws.freeze_panes = "B2"
    # Number format: rates to 4 decimals, cums to 1. month column is
    # an integer. Apply via column letter so openpyxl writes the format
    # into the column-level XF.
    for col_idx in range(2, len(header) + 1):
        # Even columns (B, D, F, ...) are *_rate; odd are *_cum.
        # 1-indexed: rate at 2, cum at 3, rate at 4, cum at 5, …
        is_rate = (col_idx % 2) == 0
        fmt = "0.0000" if is_rate else "0.0"
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].number_format = fmt
        ws.column_dimensions[letter].width = 16
    ws.column_dimensions["A"].width = 10


def _build_workbook(deal: Deal, curves: list[TypeCurve], session: Session) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    # Workbook ships with a default empty sheet; remove it so the first
    # curve's metadata sheet is the active sheet on open.
    default = wb.active
    wb.remove(default)

    used_slugs: set[str] = set()
    for tc in curves:
        slug = _sheet_slug(tc.name, used_slugs)
        meta_ws = wb.create_sheet(f"{slug} — meta")
        _write_metadata_sheet(meta_ws, tc, session)
        forecast_ws = wb.create_sheet(f"{slug} — forecast")
        _write_forecast_sheet(forecast_ws, tc)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_FILENAME_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


@router.get("/{deal_id}/export.xlsx")
def export_deal(
    deal_id: uuid.UUID, session: Session = Depends(get_session)
) -> Response:
    deal = _load_or_404(session, deal_id)
    curves = session.execute(
        select(TypeCurve)
        .where(TypeCurve.deal_id == deal_id)
        .order_by(TypeCurve.name)
    ).scalars().all()
    if not curves:
        raise HTTPException(status_code=400, detail="deal has no curves assigned")
    content = _build_workbook(deal, list(curves), session)
    safe_name = _FILENAME_SLUG_RE.sub("_", deal.name)[:64] or "deal"
    log.info(
        "deal_exported",
        id=str(deal_id),
        n_curves=len(curves),
        bytes=len(content),
    )
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.xlsx"',
        },
    )
