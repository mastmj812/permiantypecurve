"""Type curve API.

  POST   /api/type-curves/compute             live preview (no persist)
  POST   /api/type-curves                     save a new type curve
  GET    /api/type-curves                     list saved curves
  GET    /api/type-curves/{id}                fetch one curve (incl. series)
  PATCH  /api/type-curves/{id}                rename or update notes
  DELETE /api/type-curves/{id}                delete
  POST   /api/type-curves/{id}/versions       save-as-new-version
  GET    /api/type-curves/{id}/export         CSV (zip) download
"""

from __future__ import annotations

import csv
import io
import uuid
import zipfile
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AlignmentMethod, Deal, NormalizationBasis, TypeCurve
from app.db.session import get_session
from app.type_curves.aggregate import (
    AlignmentMethod as AggAlign,
    NormalizationBasis as AggBasis,
    PERCENTILE_KEYS,
    aggregate,
    serialize_aggregate,
)
from app.type_curves.loader import load_well_series

router = APIRouter(prefix="/type-curves", tags=["type_curves"])
log = get_logger("api.type_curves")


# ============================ schemas ============================


class ComputeRequest(BaseModel):
    api14s: list[str] = Field(min_length=1, max_length=500)
    normalization_basis: str = "per_lateral_ft"
    # Default is first-prod-month — includes ramp-up months, which the
    # economics team needs for cash-flow modeling. peak_month is still
    # supported for engineers doing pure decline-curve analysis.
    alignment_method: str = "first_prod_month"
    n_months: int | None = Field(default=None, ge=1, le=240)


class ComputeResponse(BaseModel):
    n_months: int
    n_wells: int
    normalization_basis: str
    alignment_method: str
    streams: dict[str, Any]


class FitOverride(BaseModel):
    """Manual override of the Arps + ramp fit for one stream."""
    qi: float
    Di: float
    b: float
    Df: float
    qo: float
    peak_index: int = Field(ge=0, le=11)


class SaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    notes: str | None = None
    filter_spec: dict[str, Any] = Field(default_factory=dict)
    included_api14s: list[str] = Field(min_length=1, max_length=500)
    normalization_basis: str = "per_lateral_ft"
    alignment_method: str = "first_prod_month"
    n_months: int | None = Field(default=None, ge=1, le=240)
    # Optional per-stream tweaked fits. When present, the computed series'
    # streams[<stream>].fitted is replaced with a re-evaluation of these
    # params and marked manual_override=True. Keys: "oil" / "gas" / "water".
    fit_overrides: dict[str, FitOverride] | None = None


class TypeCurvePreviewRequest(BaseModel):
    """Live preview body for the manual tweak panel — no persistence."""
    qi: float
    Di: float
    b: float
    Df: float
    qo: float
    peak_index: int = Field(ge=0, le=11)
    n_months: int = Field(ge=1, le=240)


class TypeCurvePreviewResponse(BaseModel):
    smoothed_rate: list[float]
    eur_per_unit: float
    ramp_eur: float
    arps_eur: float


class TypeCurveRow(BaseModel):
    id: uuid.UUID
    name: str
    notes: str | None
    filter_spec: dict[str, Any]
    included_api14s: list[str]
    normalization_basis: NormalizationBasis
    alignment_method: AlignmentMethod
    series: dict[str, Any]
    created_at: datetime
    version_of: uuid.UUID | None
    deal_id: uuid.UUID | None

    @classmethod
    def from_orm_row(cls, tc: TypeCurve) -> "TypeCurveRow":
        return cls(
            id=tc.id,
            name=tc.name,
            notes=tc.notes,
            filter_spec=tc.filter_spec or {},
            included_api14s=tc.included_api14s or [],
            normalization_basis=tc.normalization_basis,
            alignment_method=tc.alignment_method,
            series=tc.series or {},
            created_at=tc.created_at,
            version_of=tc.version_of,
            deal_id=tc.deal_id,
        )


class TypeCurveSummary(BaseModel):
    """Lightweight row for the library list — strips the bulky `series`."""

    id: uuid.UUID
    name: str
    notes: str | None
    normalization_basis: NormalizationBasis
    alignment_method: AlignmentMethod
    n_wells: int
    created_at: datetime
    version_of: uuid.UUID | None
    deal_id: uuid.UUID | None


class PatchRequest(BaseModel):
    name: str | None = None
    notes: str | None = None
    # Per-stream manual override of the fitted curve. When provided the
    # PATCH handler re-evaluates each stream's ramp+Arps via
    # `evaluate_fit` and persists into series.streams[<stream>].fitted.
    fit_overrides: dict[str, FitOverride] | None = None
    # Deal assignment. None vs not-present is meaningful here: explicit
    # null un-assigns, omission leaves the current deal_id untouched.
    # The PATCH handler distinguishes these via `model_fields_set`.
    deal_id: uuid.UUID | None = None


# ============================ helpers ============================


def _validate_basis(basis: str) -> AggBasis:
    if basis not in ("per_lateral_ft", "per_proppant_lb", "per_well"):
        raise HTTPException(status_code=400, detail=f"invalid normalization_basis: {basis}")
    return basis  # type: ignore[return-value]


def _validate_alignment(method: str) -> AggAlign:
    if method not in ("peak_month", "first_prod_month"):
        raise HTTPException(status_code=400, detail=f"invalid alignment_method: {method}")
    return method  # type: ignore[return-value]


def _apply_fit_overrides(
    payload: dict[str, Any],
    overrides: dict[str, FitOverride] | None,
) -> dict[str, Any]:
    """Mutate `payload['streams'][stream]['fitted']` for each override.

    The override path uses the same `evaluate_fit` helper the preview
    endpoint uses, so live preview and persisted save can't diverge.
    Unknown stream keys are ignored — the API validates them as
    "oil"/"gas"/"water" at the request boundary.
    """
    if not overrides:
        return payload
    from app.type_curves.fit_p50 import evaluate_fit

    n_months = int(payload.get("n_months") or 0)
    if n_months <= 0:
        return payload
    streams = payload.setdefault("streams", {})
    for stream_name, ovr in overrides.items():
        if stream_name not in streams:
            continue
        streams[stream_name]["fitted"] = evaluate_fit(
            qi=ovr.qi, Di=ovr.Di, b=ovr.b, Df=ovr.Df,
            qo=ovr.qo, peak_index=ovr.peak_index,
            n_months=n_months,
        )
    return payload


def _compute(
    session: Session,
    api14s: list[str],
    *,
    basis: str,
    alignment: str,
    n_months: int | None,
) -> dict[str, Any]:
    validated_alignment = _validate_alignment(alignment)
    wells = load_well_series(session, api14s, alignment=validated_alignment)
    if not wells:
        raise HTTPException(
            status_code=400,
            detail=(
                "no wells with both forecasts and production found "
                "(first_prod_date required for first_prod_month alignment)"
            ),
        )
    agg = aggregate(
        wells,
        normalization_basis=_validate_basis(basis),
        alignment_method=validated_alignment,
        n_months=n_months,
    )
    # Fit an Arps decline to each stream's P50. This is "the type curve"
    # in operator parlance — the smooth analytical line they publish on
    # top of the raw cross-well median. Mutate StreamSeries in place via
    # dataclasses.replace since the dataclass is frozen.
    from dataclasses import replace

    from app.type_curves.fit_p50 import fit_p50_series

    # Fit Arps to the P50 (for the smoothed-overlay line) AND to every
    # percentile + mean (for the 50-yr EUR column). The P50 fit drives
    # the chart; the per-percentile fits drive the EUR table — without
    # them, the EUR cells would just be data-window cumsums, which
    # undershoot the per-well projection by ~25–45% on Permian
    # unconventionals (most of EUR lives in the year-5+ tail).
    fitted_streams = {}
    for stream_name, stream in agg.streams.items():
        fitted = fit_p50_series(stream.p50)
        fitted_eur: dict[str, float | None] = {}
        fitted_per_pct: dict[str, dict[str, Any] | None] = {}
        for key in ("p10", "p25", "p50", "p75", "p90", "mean"):
            series = getattr(stream, key)
            r = fit_p50_series(series)
            # Keep the full fit (qi/Di/b/Df/qo/peak_index/…) so the CSV
            # export can re-evaluate each percentile out to 600 months.
            # `r` includes a `smoothed_rate` field bounded by n_months —
            # we drop that here to keep the persisted JSON small; the
            # export evaluates a fresh smoothed_rate at its target
            # horizon from the parameters alone.
            fitted_eur[key] = r["eur_per_unit"] if r is not None else None
            if r is not None:
                fitted_per_pct[key] = {
                    k: v for k, v in r.items() if k != "smoothed_rate"
                }
            else:
                fitted_per_pct[key] = None
        fitted_streams[stream_name] = replace(
            stream,
            fitted=fitted,
            fitted_eur_per_unit=fitted_eur,
            fitted_per_percentile=fitted_per_pct,
        )
    agg = replace(agg, streams=fitted_streams)
    return serialize_aggregate(agg)


# ============================ compute (preview) ============================


@router.post("/compute", response_model=ComputeResponse)
def compute_type_curve(
    req: ComputeRequest, session: Session = Depends(get_session)
) -> ComputeResponse:
    payload = _compute(
        session, req.api14s,
        basis=req.normalization_basis,
        alignment=req.alignment_method,
        n_months=req.n_months,
    )
    return ComputeResponse(**payload)


# ============================ preview (tweak) ============================


@router.post("/preview", response_model=TypeCurvePreviewResponse)
def preview_type_curve_fit(req: TypeCurvePreviewRequest) -> TypeCurvePreviewResponse:
    """Evaluate a ramp+Arps fit for the manual-tweak panel.

    Stateless — no DB hit. Reuses `evaluate_fit` so the preview and the
    persisted-on-save curve are byte-for-byte identical for the same
    inputs.
    """
    from app.type_curves.fit_p50 import evaluate_fit

    fitted = evaluate_fit(
        qi=req.qi, Di=req.Di, b=req.b, Df=req.Df,
        qo=req.qo, peak_index=req.peak_index,
        n_months=req.n_months,
    )
    return TypeCurvePreviewResponse(
        smoothed_rate=fitted["smoothed_rate"],
        eur_per_unit=fitted["eur_per_unit"],
        ramp_eur=fitted["ramp_eur"],
        arps_eur=fitted["arps_eur"],
    )


# ============================ save ============================


def _persist(
    session: Session,
    *,
    payload: dict[str, Any],
    req: SaveRequest,
    version_of: uuid.UUID | None,
) -> TypeCurve:
    row = TypeCurve(
        id=uuid.uuid4(),
        name=req.name,
        notes=req.notes,
        filter_spec=req.filter_spec,
        included_api14s=req.included_api14s,
        normalization_basis=NormalizationBasis(req.normalization_basis),
        alignment_method=AlignmentMethod(req.alignment_method),
        series=payload,
        version_of=version_of,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.post("", response_model=TypeCurveRow, status_code=201)
def save_type_curve(
    req: SaveRequest, session: Session = Depends(get_session)
) -> TypeCurveRow:
    payload = _compute(
        session, req.included_api14s,
        basis=req.normalization_basis,
        alignment=req.alignment_method,
        n_months=req.n_months,
    )
    payload = _apply_fit_overrides(payload, req.fit_overrides)
    row = _persist(session, payload=payload, req=req, version_of=None)
    log.info(
        "type_curve_saved",
        id=str(row.id),
        n_wells=len(req.included_api14s),
        overridden_streams=list(req.fit_overrides.keys()) if req.fit_overrides else [],
    )
    return TypeCurveRow.from_orm_row(row)


@router.post("/{type_curve_id}/versions", response_model=TypeCurveRow, status_code=201)
def save_as_new_version(
    type_curve_id: uuid.UUID,
    req: SaveRequest,
    session: Session = Depends(get_session),
) -> TypeCurveRow:
    parent = session.get(TypeCurve, type_curve_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="parent type curve not found")
    payload = _compute(
        session, req.included_api14s,
        basis=req.normalization_basis,
        alignment=req.alignment_method,
        n_months=req.n_months,
    )
    payload = _apply_fit_overrides(payload, req.fit_overrides)
    row = _persist(session, payload=payload, req=req, version_of=parent.id)
    log.info(
        "type_curve_versioned", id=str(row.id), parent=str(parent.id),
        n_wells=len(req.included_api14s),
    )
    return TypeCurveRow.from_orm_row(row)


# ============================ list / get / patch / delete ============================


@router.get("", response_model=list[TypeCurveSummary])
def list_type_curves(
    session: Session = Depends(get_session),
) -> list[TypeCurveSummary]:
    rows = session.execute(
        select(TypeCurve).order_by(TypeCurve.created_at.desc())
    ).scalars().all()
    return [
        TypeCurveSummary(
            id=r.id, name=r.name, notes=r.notes,
            normalization_basis=r.normalization_basis,
            alignment_method=r.alignment_method,
            n_wells=len(r.included_api14s or []),
            created_at=r.created_at,
            version_of=r.version_of,
            deal_id=r.deal_id,
        )
        for r in rows
    ]


@router.get("/{type_curve_id}", response_model=TypeCurveRow)
def get_type_curve(
    type_curve_id: uuid.UUID, session: Session = Depends(get_session)
) -> TypeCurveRow:
    row = session.get(TypeCurve, type_curve_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return TypeCurveRow.from_orm_row(row)


@router.patch("/{type_curve_id}", response_model=TypeCurveRow)
def patch_type_curve(
    type_curve_id: uuid.UUID,
    req: PatchRequest,
    session: Session = Depends(get_session),
) -> TypeCurveRow:
    row = session.get(TypeCurve, type_curve_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if req.name is not None:
        row.name = req.name
    if req.notes is not None:
        row.notes = req.notes
    if req.fit_overrides:
        # Mutating a JSONB field via SQLAlchemy ORM requires reassigning
        # the column (in-place mutation is invisible to the unit-of-work).
        updated_series = _apply_fit_overrides(dict(row.series or {}), req.fit_overrides)
        row.series = updated_series
    # Explicit-null-vs-omitted matters for deal_id: omitted leaves the
    # current assignment untouched; null un-assigns.
    if "deal_id" in req.model_fields_set:
        if req.deal_id is not None and session.get(Deal, req.deal_id) is None:
            raise HTTPException(status_code=404, detail="deal not found")
        row.deal_id = req.deal_id
    session.commit()
    session.refresh(row)
    return TypeCurveRow.from_orm_row(row)


@router.delete("/{type_curve_id}", status_code=204)
def delete_type_curve(
    type_curve_id: uuid.UUID, session: Session = Depends(get_session)
) -> Response:
    row = session.get(TypeCurve, type_curve_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


# ============================ CSV export ============================


# Days-per-month constant for monthly→cum conversion in the forecast
# export. Matches the convention used by the per-well fit code
# (`fit_p50.py::_DAYS_PER_MONTH`) so the exported cum matches the EUR
# values in metadata.csv to within rounding.
_DAYS_PER_MONTH = 30.4375

# Horizon for the fitted-forecast CSV. 50 years × 12 months = 600 rows.
# Matches the brief's `DEFAULT_FORECAST_HORIZON_YEARS` and the per-
# percentile EUR computation, so the cum column reaches the EUR
# asymptote at the bottom row.
_FORECAST_N_MONTHS = 600

_PERCENTILE_KEYS_WITH_MEAN = ("p10", "p25", "p50", "p75", "p90", "mean")


def _evaluate_fitted_rates(
    stream_data: dict[str, Any],
) -> dict[str, list[float] | None]:
    """Per-percentile fitted rate arrays out to ``_FORECAST_N_MONTHS``.

    Reads ``fitted_per_percentile`` from the saved series (new format)
    and falls back to refitting from the raw monthly arrays for type
    curves saved before that field existed. None for any percentile
    whose underlying series couldn't be fit (too few months, all-zero,
    etc) — same fail-open semantic as ``fitted_eur_per_unit``.

    Shared by the CSV-zip export (``_forecast_csv``) and the deal-level
    Excel export so the two stay in lockstep.
    """
    # Lazy imports — these pull in numpy / scipy and we don't want
    # that on the list endpoint's hot path.
    from app.type_curves.fit_p50 import evaluate_fit, fit_p50_series

    persisted = (stream_data or {}).get("fitted_per_percentile") or {}
    rates_by_pct: dict[str, list[float] | None] = {}
    for key in _PERCENTILE_KEYS_WITH_MEAN:
        fit = persisted.get(key)
        if fit is None:
            # Old save or unfittable series — try to refit from the
            # raw monthly series now. Stateless fallback.
            series = stream_data.get(key)
            if not series:
                rates_by_pct[key] = None
                continue
            r = fit_p50_series(series)
            fit = (
                {k: v for k, v in r.items() if k != "smoothed_rate"}
                if r is not None
                else None
            )
        if fit is None:
            rates_by_pct[key] = None
            continue
        evaluated = evaluate_fit(
            qi=fit["qi"], Di=fit["Di"], b=fit["b"], Df=fit["Df"],
            qo=fit.get("qo", fit["qi"]),
            peak_index=fit.get("peak_index", 0),
            n_months=_FORECAST_N_MONTHS,
        )
        rates_by_pct[key] = evaluated["smoothed_rate"]
    return rates_by_pct


def _forecast_csv(
    stream_data: dict[str, Any], alignment: str
) -> bytes:
    """Build {stream}_forecast.csv: per-percentile fitted rate + cum
    out to 50 years, for handoff to an economics model.

    Empty cells appear for any percentile whose underlying series
    couldn't be fit (too few months, all-zero, etc) — same fail-open
    semantic as `fitted_eur_per_unit`.
    """
    rates_by_pct = _evaluate_fitted_rates(stream_data)

    month_col = (
        "month_since_first_prod" if alignment == "first_prod_month" else "month_since_peak"
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    header: list[str] = [month_col]
    for key in _PERCENTILE_KEYS_WITH_MEAN:
        header.append(f"fitted_{key}_rate")
        header.append(f"fitted_{key}_cum")
    writer.writerow(header)

    # Track running cum per percentile so each row's cum is the
    # cumulative sum of rate × days_per_month up to that month.
    cum_by_pct: dict[str, float] = {k: 0.0 for k in _PERCENTILE_KEYS_WITH_MEAN}
    for i in range(_FORECAST_N_MONTHS):
        row: list[Any] = [i + 1]
        for key in _PERCENTILE_KEYS_WITH_MEAN:
            arr = rates_by_pct[key]
            if arr is None or i >= len(arr):
                row.append("")
                row.append("")
                continue
            rate = float(arr[i])
            cum_by_pct[key] += rate * _DAYS_PER_MONTH
            row.append(f"{rate:.6f}")
            row.append(f"{cum_by_pct[key]:.3f}")
        writer.writerow(row)
    return buf.getvalue().encode()


def _stream_csv(
    stream_name: str, stream_data: dict[str, Any], alignment: str
) -> bytes:
    # Column name reflects the alignment so downstream consumers know
    # whether month 1 is peak month or first-prod month.
    month_col = (
        "month_since_first_prod" if alignment == "first_prod_month" else "month_since_peak"
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        month_col, "p10", "p25", "p50", "p75", "p90", "mean", "well_count",
    ])
    months = len(stream_data["p50"])
    for i in range(months):
        writer.writerow([
            i + 1,
            stream_data["p10"][i],
            stream_data["p25"][i],
            stream_data["p50"][i],
            stream_data["p75"][i],
            stream_data["p90"][i],
            stream_data["mean"][i],
            stream_data["well_count"][i],
        ])
    return buf.getvalue().encode()


def _metadata_csv(tc: TypeCurve) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["field", "value"])
    writer.writerow(["id", str(tc.id)])
    writer.writerow(["name", tc.name])
    writer.writerow(["notes", tc.notes or ""])
    writer.writerow(["normalization_basis", tc.normalization_basis.value])
    writer.writerow(["alignment_method", tc.alignment_method.value])
    writer.writerow(["created_at", tc.created_at.isoformat()])
    writer.writerow(["version_of", str(tc.version_of) if tc.version_of else ""])
    writer.writerow(["n_wells", len(tc.included_api14s or [])])
    writer.writerow([])
    writer.writerow(["filter_spec_key", "filter_spec_value"])
    for k, v in (tc.filter_spec or {}).items():
        writer.writerow([k, v])
    writer.writerow([])
    writer.writerow(["implied_eur_per_1000ft (BBL or MCF)"])
    writer.writerow(["stream", *PERCENTILE_KEYS, "mean"])
    streams = (tc.series or {}).get("streams", {})
    for s_name in ("oil", "gas", "water"):
        s = streams.get(s_name, {})
        eur = s.get("implied_eur_per_1000ft", {})
        writer.writerow([
            s_name,
            *[eur.get(k, "") for k in PERCENTILE_KEYS],
            eur.get("mean", ""),
        ])
    writer.writerow([])
    writer.writerow(["included_api14s"])
    for a in tc.included_api14s or []:
        writer.writerow([a])
    return buf.getvalue().encode()


@router.get("/{type_curve_id}/export")
def export_type_curve(
    type_curve_id: uuid.UUID, session: Session = Depends(get_session)
) -> Response:
    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")

    buf = io.BytesIO()
    streams = (tc.series or {}).get("streams", {})
    alignment_val = tc.alignment_method.value
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for s_name in ("oil", "gas", "water"):
            s = streams.get(s_name)
            if s:
                zf.writestr(
                    f"{s_name}_rates.csv", _stream_csv(s_name, s, alignment_val)
                )
                # Fitted forecast out to 50 years for each percentile +
                # mean — companion to the data-window _rates.csv for
                # handoff to economics models.
                zf.writestr(
                    f"{s_name}_forecast.csv", _forecast_csv(s, alignment_val)
                )
        zf.writestr("metadata.csv", _metadata_csv(tc))

    safe_name = "".join(c if c.isalnum() else "_" for c in tc.name)[:64] or "type_curve"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
        },
    )
