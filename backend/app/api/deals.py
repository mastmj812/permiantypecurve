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
import math
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.type_curves import (
    _DAYS_PER_MONTH,
    _FITTED_PARAM_KEYS,
    _FORECAST_N_MONTHS,
    _PERCENTILE_KEYS_WITH_MEAN,
    TypeCurveSummary,
    _evaluate_fitted_rates,
    _fitted_eur_per_1000ft,
    _fitted_p50_params,
)
from app.core.logging import get_logger
from app.db.models import Deal, NormalizationBasis, TypeCurve
from app.db.models.production_monthly import ProductionMonthly
from app.db.session import get_session
from app.exports.blueox import (
    INVENTORY_CATEGORIES,
    LEVEL_TO_SPE_KEY,
    BlueOxContractError,
    BlueOxExportData,
    InventoryRow,
    ZoneData,
    blueox_filename,
    build_blueox_workbook,
    monthly_volumes_from_rates,
)
from app.exports.well_rows import (
    PER_WELL_COL_FORMATS,
    PER_WELL_HEADERS,
    per_well_rows,
)
from app.type_curves.aggregate import PERCENTILE_KEYS
from app.warehouse_client.narvi import NarviInventoryWell, fetch_narvi_inventory
from app.warehouse_client.session import get_warehouse_session

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
        n_wells=len(tc.included_api10s or []),
        created_at=tc.created_at,
        version_of=tc.version_of,
        deal_id=tc.deal_id,
        is_stale=bool(tc.is_stale),
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


def _write_metadata_sheet(
    ws: Any, tc: TypeCurve, session: Session | None
) -> None:
    """Two-column field/value layout mirroring ``_metadata_csv`` for the
    top section, then a wide per-well summary block at the bottom that
    expands the bare api10 list into completion-design + EUR metrics
    the engineer wants alongside the type-curve params. The CSV-zip
    export still ships just the api10 list — when an engineer needs
    the wider context they're already in xlsx territory.

    ``session`` may be None — the per-well summary block is then
    skipped (the rest of the metadata sheet still renders). Used by
    pure-function workbook-shape tests that don't stand up a DB."""
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
    # SPE / PRMS reserves orientation: the p10 columns are the HIGH
    # case (exceeded by 10% of wells), p90 the LOW case. Stamped here
    # so downstream econ tooling can't misread the percentile columns.
    ws.append(["percentile_convention", "SPE (P10 = high case, P90 = low case)"])
    ws.append(["created_at", tc.created_at.isoformat()])
    ws.append(["version_of", str(tc.version_of) if tc.version_of else ""])
    ws.append(["n_wells", len(tc.included_api10s or [])])
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

    # Per-well summary block — expands the bare api10 list into a wide
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
    if session is not None:
        for row in per_well_rows(session, list(tc.included_api10s or [])):
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

    # Trapezoid rule: month K's volume = (rate at start + rate at end)
    # / 2 * dt, last month flat-extrapolated. Mirrors the frontend's
    # cumulateNumericArray + the metadata sheet's _fitted_eur_per_1000ft
    # so the xlsx cum column tracks the closed-form scipy.quad EUR
    # (= PPTX value, = UI EUR-table value) to <0.1% on Permian fits.
    # The older right-endpoint rectangle rule had a ~0.2% bias.
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
                nxt = arr[i + 1] if i + 1 < len(arr) else None
                b = (
                    float(nxt)
                    if nxt is not None and math.isfinite(float(nxt))
                    else rate
                )
                cum_by_pct[key] += (rate + b) / 2.0 * _DAYS_PER_MONTH
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


def _build_workbook(
    deal: Deal, curves: list[TypeCurve], session: Session | None = None
) -> bytes:
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


# ==================== Blue Ox curve-drop export ====================
#
# Deliverable A of the Blue Ox Engineering Curve-Drop Contract v1
# (engineering_db docs/blue_ox_curve_drop_contract.md). Assembly only —
# workbook shape, validation, and the SPE->ascending percentile flip
# live in app.exports.blueox (pure module, no DB/HTTP coupling).


class BlueOxInventoryRowIn(BaseModel):
    """One manual inventory-sheet row (contract §1.3 + category
    amendment)."""

    drilled_lateral_ft: float = Field(ge=3_000, le=25_000)
    # Analog lateral basis behind the curve; defaults to the zone's
    # analog-cohort mean lateral when omitted.
    producing_lateral_ft: float | None = Field(default=None, ge=3_000, le=25_000)
    well_name: str | None = None
    category: Literal["PDP", "PUD", "UPSIDE"] = "PUD"


class BlueOxZoneSpec(BaseModel):
    type_curve_id: uuid.UUID
    # Adopted verbatim by Blue Ox as the zone identifier (contract
    # Principle 2 — stable across re-drops). Defaults to the curve name.
    zone_name: str | None = Field(default=None, min_length=1, max_length=26)
    reserve_category: Literal["PUD", "RES"]
    # narvi formation_blueox bench codes whose planned wells belong to
    # this zone (a zone commonly spans benches: WCA_1 + WCA_2 -> WCA).
    # Benches must be disjoint across zones.
    benches: list[str] = Field(default_factory=list)
    # Manual rows — the fallback for deals without narvi coverage, or
    # extras on top of the narvi pull.
    inventory: list[BlueOxInventoryRowIn] = Field(default_factory=list)


class NarviSelection(BaseModel):
    """One narvi scenario feeding this drop's inventory. A drop may
    merge several (one per DSU, plus geometry-forced splits — e.g.
    U-turn wells in half a DSU saved as their own scenario)."""

    deal_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)


class BlueOxExportRequest(BaseModel):
    codename: str = Field(min_length=1, max_length=64)
    curve_months: int = Field(default=360, ge=12, le=600)
    # Blue Ox ASCENDING labels (their P10 = low case); the flip to our
    # SPE fits happens in assembly via LEVEL_TO_SPE_KEY.
    levels: list[Literal["P10", "P25", "P75", "P90"]] = Field(default_factory=list)
    prepared_by: str = Field(min_length=1, max_length=128)
    zones: list[BlueOxZoneSpec] = Field(min_length=1)
    # narvi scenarios whose inventory_well rows populate the inventory
    # sheet (via each zone's `benches` mapping).
    narvi_selections: list[NarviSelection] = Field(default_factory=list)
    # Benches present in the selected scenarios but deliberately NOT in
    # this workbook (e.g. carried in a sibling deal's drop). Declared in
    # the manifest; an unmapped bench that is not excluded is a hard
    # error — silently dropping wells would understate the location
    # count Blue Ox values.
    exclude_benches: list[str] = Field(default_factory=list)


_BLUEOX_BASIS: dict[NormalizationBasis, str] = {
    NormalizationBasis.PER_LATERAL_FT: "per_1000_lateral_ft",
    NormalizationBasis.PER_WELL: "per_well",
}
_BLUEOX_QI_UNITS: dict[str, str] = {"oil": "bbl/d", "gas": "Mcf/d"}
_BLUEOX_SOURCE_SYSTEM = "anduin type-curve DB (synced from oilgas warehouse)"


def _handoff_category(w: NarviInventoryWell) -> str:
    """PDP / PUD / UPSIDE for one narvi well.

    narvi's user-facing category (auto-derived there and overridable in
    its UI, like the TVD override) wins when persisted. Fallback is the
    agreed rule: existing producers -> PDP; pdp_count_3mi >= 3 -> PUD;
    <= 2 or unscored -> UPSIDE.
    """
    if w.handoff_category:
        cat = w.handoff_category.strip().upper()
        if cat in INVENTORY_CATEGORIES:
            return cat
    if (w.category or "").lower() == "pdp":
        return "PDP"
    if w.pdp_count_3mi is not None and w.pdp_count_3mi >= 3:
        return "PUD"
    return "UPSIDE"


def _one_yr_effective(di_nominal: float, b: float) -> float | None:
    """1-yr secant effective decline from nominal Di (per-yr) and Arps b.

    Quoted in curve_params notes so the finance side reads both
    conventions — a nominal 2.5/yr Permian Di is ~75% effective and the
    bare nominal number invites misreading.
    """
    try:
        if b > 0:
            return 1.0 - float((1.0 + b * di_nominal) ** (-1.0 / b))
        return 1.0 - math.exp(-di_nominal)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def _collect_blueox_zone(
    session: Session,
    tc: TypeCurve,
    spec: BlueOxZoneSpec,
    delivered: list[str],
    curve_months: int,
    errors: list[str],
    narvi_wells: list[NarviInventoryWell] | None = None,
) -> ZoneData | None:
    """Assemble one zone's volumes / params / analogs / inventory.

    Appends human-readable problems to ``errors`` and returns None when
    the zone can't be assembled (missing fits, unsupported basis, no
    usable producing-lateral default).
    """
    from app.api.type_curves import _fitted_params_by_pct
    from app.type_curves.fit_p50 import evaluate_fit

    zone_name = spec.zone_name or tc.name
    basis = _BLUEOX_BASIS.get(tc.normalization_basis)
    if basis is None:
        errors.append(
            f"{zone_name}: normalization basis "
            f"{tc.normalization_basis.value!r} is not exportable under the contract"
        )
        return None

    streams = (tc.series or {}).get("streams", {})
    fits_by_stream = {
        s: _fitted_params_by_pct(streams.get(s) or {})
        for s in ("oil", "gas", "water")
    }

    def _rates(fit: dict[str, Any]) -> list[float]:
        evaluated = evaluate_fit(
            qi=fit["qi"], Di=fit["Di"], b=fit["b"], Df=fit["Df"],
            qo=fit.get("qo", fit["qi"]),
            peak_index=fit.get("peak_index", 0),
            n_months=_FORECAST_N_MONTHS,
        )
        return [float(r) for r in evaluated["smoothed_rate"]]

    volumes: dict[str, dict[str, list[float]]] = {}
    params_rows: list[dict[str, Any]] = []
    for lv in delivered:
        spe_key = LEVEL_TO_SPE_KEY[lv]
        by_stream: dict[str, list[float]] = {}
        for stream in ("oil", "gas"):
            fit = fits_by_stream[stream].get(spe_key)
            if fit is None:
                errors.append(
                    f"{zone_name}: no {stream} fit behind level {lv} (SPE "
                    f"{spe_key}) — a partial triplet is a contract failure; "
                    "drop the level or re-save the curve"
                )
                continue
            by_stream[stream] = monthly_volumes_from_rates(
                _rates(fit), curve_months, _DAYS_PER_MONTH
            )
            di = float(fit["Di"])
            b_factor = float(fit["b"])
            peak_month = int(fit.get("peak_index", 0)) + 1
            eff = _one_yr_effective(di, b_factor)
            eff_txt = f"; 1-yr secant effective {eff * 100.0:.1f}%" if eff is not None else ""
            qi_units = _BLUEOX_QI_UNITS[stream]
            if basis == "per_1000_lateral_ft":
                qi_units += " per 1,000 ft"
            params_rows.append({
                "stream": stream,
                "level": lv,
                "qi": float(fit["qi"]),
                "qi_units": qi_units,
                "b_factor": b_factor,
                "di": di,
                "dmin": float(fit["Df"]),
                "notes": (
                    f"{tc.alignment_method.value} alignment; qi at peak "
                    f"month {peak_month}{eff_txt}"
                ),
            })
        # Water rides on the base level only (contract: no percentile
        # water columns); included when a water fit exists.
        if lv == "P50":
            wfit = fits_by_stream["water"].get("p50")
            if wfit is not None:
                by_stream["water"] = monthly_volumes_from_rates(
                    _rates(wfit), curve_months, _DAYS_PER_MONTH
                )
        volumes[lv] = by_stream

    analog_rows = per_well_rows(session, list(tc.included_api10s or []))
    lat_idx = PER_WELL_HEADERS.index("Lateral Length")
    laterals = [float(r[lat_idx]) for r in analog_rows if r[lat_idx]]
    cohort_mean_lat = sum(laterals) / len(laterals) if laterals else None

    inventory: list[InventoryRow] = []
    # narvi wells first: completed lateral (EUR driver) is the producing
    # lateral, drilled (legs + turn arc) drives per-ft capex. Category
    # comes from narvi (override-aware) via _handoff_category.
    for nw in narvi_wells or []:
        if nw.completed_lateral_ft is None or nw.drilled_lateral_ft is None:
            errors.append(
                f"{zone_name}: narvi well {nw.well_name!r} "
                f"({nw.deal_id}/{nw.scenario_id}) has no laterals persisted"
            )
            continue
        inventory.append(InventoryRow(
            producing_lateral_ft=nw.completed_lateral_ft,
            drilled_lateral_ft=nw.drilled_lateral_ft,
            well_name=nw.well_name,
            category=_handoff_category(nw),
        ))
    # Manual rows — fallback / extras on top of the narvi pull.
    for i, inv in enumerate(spec.inventory, start=1):
        producing = inv.producing_lateral_ft or cohort_mean_lat
        if producing is None:
            errors.append(
                f"{zone_name}: inventory row {i} has no producing_lateral_ft "
                "and the analog cohort has no laterals to default from"
            )
            continue
        inventory.append(InventoryRow(
            producing_lateral_ft=float(producing),
            drilled_lateral_ft=float(inv.drilled_lateral_ft),
            well_name=inv.well_name,
            category=inv.category,
        ))

    return ZoneData(
        zone_name=zone_name,
        reserve_category=spec.reserve_category,
        normalization_basis=basis,
        volumes=volumes,
        curve_params=params_rows,
        analog_headers=PER_WELL_HEADERS,
        analog_rows=analog_rows,
        inventory=inventory,
    )


def _fetch_narvi_by_zone(
    req: BlueOxExportRequest, errors: list[str]
) -> tuple[dict[str, list[NarviInventoryWell]], tuple[str, ...], list[NarviInventoryWell]]:
    """Pull the selected narvi scenarios and distribute wells to zones
    by each zone's bench list.

    Returns ({zone_name: wells}, manifest exclusion strings, unzoned
    PDP wells). Every fetched PLANNED well must land in a zone or an
    explicitly excluded bench — an unmapped bench is an error, never a
    silent drop (the inventory row count IS the location count Blue Ox
    values). Existing producers are NEVER dropped: a PDP well whose
    bench has no zone here comes back in the third element and lands on
    the inventory sheet with its bench code as `area` (the downstream
    gunbarrel automation needs the whole DSU stack).
    """
    if not req.narvi_selections:
        return {}, (), []

    bench_to_zone: dict[str, str] = {}
    for spec in req.zones:
        zname = spec.zone_name or ""
        for bench in spec.benches:
            if bench in bench_to_zone:
                errors.append(
                    f"bench {bench!r} mapped to more than one zone "
                    f"({bench_to_zone[bench]!r} and {zname!r})"
                )
            bench_to_zone[bench] = zname

    pairs = [(sel.deal_id, sel.scenario_id) for sel in req.narvi_selections]
    try:
        with contextmanager(get_warehouse_session)() as wh:
            wells = fetch_narvi_inventory(wh, pairs)
    except Exception as exc:  # surface as a 422, not a 500
        errors.append(f"narvi inventory fetch failed: {exc}")
        return {}, (), []

    seen_pairs = {(w.deal_id, w.scenario_id) for w in wells}
    for pair in pairs:
        if pair not in seen_pairs:
            errors.append(
                f"narvi selection {pair[0]}/{pair[1]} matched no inventory wells"
            )

    # EXISTING producers (narvi category 'pdp', well_name = api10) ride
    # along as display rows for the downstream gunbarrel automation.
    # They dedupe across merged scenarios (the same producer shows up
    # in several DSU plans) and are NEVER dropped — unmapped/excluded
    # benches route them to the unzoned list (bench code as area).
    # Planned wells (everything else) must map or be excluded, and must
    # NOT duplicate across merged scenarios.
    planned = [w for w in wells if (w.category or "").lower() != "pdp"]
    pdp_context: list[NarviInventoryWell] = []
    seen_pdp: set[tuple[str, str]] = set()
    for w in wells:
        if (w.category or "").lower() != "pdp":
            continue
        key = (w.well_name, w.formation or "")
        if key not in seen_pdp:
            seen_pdp.add(key)
            pdp_context.append(w)

    seen_names: dict[tuple[str, str], tuple[str, str]] = {}
    for w in planned:
        key = (w.well_name, w.formation or "")
        if key in seen_names and seen_names[key] != (w.deal_id, w.scenario_id):
            errors.append(
                f"planned well {w.well_name!r} ({w.formation}) appears in both "
                f"{seen_names[key][1]} and {w.scenario_id}"
            )
        seen_names[key] = (w.deal_id, w.scenario_id)

    excluded = dict.fromkeys(req.exclude_benches, 0)
    unmapped: dict[str, int] = {}
    by_zone: dict[str, list[NarviInventoryWell]] = {}
    for w in planned:
        bench = w.formation or "(null)"
        if bench in excluded:
            excluded[bench] += 1
            continue
        zone = bench_to_zone.get(bench)
        if zone is None:
            unmapped[bench] = unmapped.get(bench, 0) + 1
            continue
        by_zone.setdefault(zone, []).append(w)
    unzoned_pdp: list[NarviInventoryWell] = []
    for w in pdp_context:
        zone = bench_to_zone.get(w.formation or "")
        if zone is None or (w.formation or "") in excluded:
            unzoned_pdp.append(w)
            continue
        by_zone.setdefault(zone, []).append(w)
    if unmapped:
        errors.append(
            "narvi benches with no zone mapping and no exclusion: "
            + ", ".join(f"{b} ({n} wells)" for b, n in sorted(unmapped.items()))
        )
    for bench, n in excluded.items():
        if n == 0:
            errors.append(f"exclude_benches entry {bench!r} matched no wells")
    exclusions = tuple(
        f"{b} ({n} wells)" for b, n in sorted(excluded.items()) if n > 0
    )
    return by_zone, exclusions, unzoned_pdp


def _collect_blueox_data(
    session: Session, deal: Deal, req: BlueOxExportRequest, export_date: date
) -> BlueOxExportData:
    """Assemble the full pure-builder input; 422 on any assembly problem."""
    errors: list[str] = []
    delivered = [
        lv for lv in ("P10", "P25", "P50", "P75", "P90")
        if lv == "P50" or lv in req.levels
    ]

    # Zone names must be resolved before the narvi distribution (the
    # bench map keys on them), so default them from the curve names
    # up front.
    for spec in req.zones:
        if spec.zone_name is None:
            tc = session.get(TypeCurve, spec.type_curve_id)
            if tc is not None:
                spec.zone_name = tc.name[:26].strip()

    narvi_by_zone, inventory_exclusions, unzoned_pdp = _fetch_narvi_by_zone(req, errors)

    # Existing producers whose bench has no zone in THIS workbook still
    # ride the inventory sheet (bench code as `area`) — the gunbarrel
    # automation needs the whole DSU stack, zone coverage or not.
    pdp_context_rows: list[tuple[str, InventoryRow]] = []
    for w in unzoned_pdp:
        if w.completed_lateral_ft is None or w.drilled_lateral_ft is None:
            log.info("blueox_pdp_context_missing_laterals", well=w.well_name)
            continue
        pdp_context_rows.append((
            w.formation or "(unknown)",
            InventoryRow(
                producing_lateral_ft=w.completed_lateral_ft,
                drilled_lateral_ft=w.drilled_lateral_ft,
                well_name=w.well_name,
                category="PDP",
            ),
        ))

    zones: list[ZoneData] = []
    curves: list[TypeCurve] = []
    all_apis: set[str] = set()
    for spec in req.zones:
        tc = session.get(TypeCurve, spec.type_curve_id)
        if tc is None:
            errors.append(f"type curve {spec.type_curve_id} not found")
            continue
        if tc.deal_id != deal.id:
            # Allowed on purpose: one curve can legitimately serve zones
            # in two workbooks (e.g. a formation whose curve lives on a
            # sibling deal but whose planned wells sit in this deal's
            # DSUs). Logged so a mis-picked curve is visible.
            log.info(
                "blueox_cross_deal_curve",
                deal=deal.name,
                curve=tc.name,
                curve_deal_id=str(tc.deal_id),
            )
        curves.append(tc)
        zone = _collect_blueox_zone(
            session, tc, spec, delivered, req.curve_months, errors,
            narvi_wells=narvi_by_zone.get(spec.zone_name or tc.name, []),
        )
        if zone is not None:
            zones.append(zone)
            all_apis.update(str(r[0]) for r in zone.analog_rows)

    prod_rows_db = session.execute(
        select(ProductionMonthly)
        .where(ProductionMonthly.api10.in_(sorted(all_apis)))
        .order_by(ProductionMonthly.api10, ProductionMonthly.prod_date)
    ).scalars().all() if all_apis else []

    if not prod_rows_db:
        errors.append("no monthly production history found for any analog well")
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    # Optional columns obey the no-blank-cells rule: water is included
    # (None coalesced to 0, the standard load-time convention) whenever
    # any month reports it; days_on only when EVERY month has it — a
    # zero producing_days means shut-in, not missing, so None can't be
    # coalesced there.
    has_water = any(r.water_bbl is not None for r in prod_rows_db)
    has_days = all(r.producing_days is not None for r in prod_rows_db)
    headers = ["api10", "date", "oil_bbl", "gas_mcf"]
    if has_water:
        headers.append("water_bbl")
    if has_days:
        headers.append("days_on")
    prod_rows: list[list[Any]] = []
    for r in prod_rows_db:
        row: list[Any] = [
            r.api10,
            r.prod_date.strftime("%Y-%m"),
            float(r.oil_bbl or 0.0),
            float(r.gas_mcf or 0.0),
        ]
        if has_water:
            row.append(float(r.water_bbl or 0.0))
        if has_days:
            row.append(float(r.producing_days or 0.0))
        prod_rows.append(row)

    history_through = max(r.prod_date for r in prod_rows_db).strftime("%Y-%m")
    history_exceptions = tuple(sorted(all_apis - {r.api10 for r in prod_rows_db}))

    filename = blueox_filename(req.codename, export_date)
    return BlueOxExportData(
        codename=req.codename,
        export_date=export_date,
        curve_months=req.curve_months,
        levels=tuple(lv for lv in delivered if lv != "P50"),
        prepared_by=req.prepared_by,
        source_system=_BLUEOX_SOURCE_SYSTEM,
        governing_export=f"deal {deal.name} ({deal.id}) — {filename}",
        curve_params_source="; ".join(
            f"{tc.name} ({tc.id}) saved {tc.created_at:%Y-%m-%d}" for tc in curves
        ),
        zones=zones,
        production_headers=headers,
        production_rows=prod_rows,
        production_history_through=history_through,
        history_exceptions=history_exceptions,
        inventory_exclusions=inventory_exclusions,
        pdp_context_rows=pdp_context_rows,
    )


@router.post("/{deal_id}/blueox-export.xlsx")
def export_deal_blueox(
    deal_id: uuid.UUID,
    req: BlueOxExportRequest,
    session: Session = Depends(get_session),
) -> Response:
    deal = _load_or_404(session, deal_id)
    export_date = datetime.now(UTC).date()
    data = _collect_blueox_data(session, deal, req, export_date)
    try:
        content = build_blueox_workbook(data)
    except BlueOxContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    filename = blueox_filename(req.codename, export_date)
    log.info(
        "deal_blueox_exported",
        id=str(deal_id),
        codename=req.codename,
        n_zones=len(data.zones),
        curve_months=req.curve_months,
        levels=list(data.levels),
        bytes=len(content),
    )
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
