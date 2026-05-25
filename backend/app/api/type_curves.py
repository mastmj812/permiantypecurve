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

import copy
import csv
import io
import math
import uuid
import zipfile
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.logging import get_logger
from app.db.models import AlignmentMethod, Deal, Forecast, NormalizationBasis, Stream, TypeCurve, Well
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


class TypeCurveWellStat(BaseModel):
    """Per-well oil EUR + lateral, for the slide-export probit chart.

    EUR is the persisted 50-yr Arps integral on the well's oil forecast
    (forecasts.eur). EUR/ft is EUR divided by lateral_ft — undefined and
    returned null when either is missing or lateral_ft <= 0.
    """

    api14: str
    name: str | None
    lateral_ft: float | None
    oil_eur: float | None
    oil_eur_per_ft: float | None


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
    """Apply a per-stream manual override of the published P50 fit.

    The saved type-curve JSON carries two parallel stores of fit params:

      * ``streams[s]["fitted"]`` — "the published curve". The chart's
        solid central line and the EUR table's "Fitted P50" cell read
        from here. Overwritten by the override.
      * ``streams[s]["fitted_per_percentile"]`` — independent Arps fits
        to each percentile's rate series. The right-column full-forecast
        chart bands, the export's forecast tab (per-percentile rate +
        cum columns), and the metadata sheet's fitted_eur_per_1000ft
        row read from here. ``.p50`` here is the auto-fit to the P50
        rate series — separate from the published fit above.

    Historically the override only touched ``fitted``, leaving
    ``fitted_per_percentile.p50`` at the auto-fit value. That meant
    Update Fit changed what the user saw in the UI's central line but
    not what the export's P50 forecast columns / metadata EUR row
    reported — same-input deal exports came out byte-identical no
    matter how much the engineer tweaked. Now both stores are written
    together so the override flows through to every downstream consumer.

    The other five percentile slots (P10/P25/P75/P90/mean) stay at
    their auto-fit values — the TweakPanel only adjusts the P50 curve,
    not the whole distribution, by design.

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
        fitted = evaluate_fit(
            qi=ovr.qi, Di=ovr.Di, b=ovr.b, Df=ovr.Df,
            qo=ovr.qo, peak_index=ovr.peak_index,
            n_months=n_months,
        )
        streams[stream_name]["fitted"] = fitted

        # Mirror into the per-percentile P50 slot so the export +
        # metadata sheet + full-forecast chart band match the published
        # fit. Use the same shape the auto-fit emits there: strip
        # smoothed_rate (recomputable from params; keeps the JSON small).
        per_pct = streams[stream_name].get("fitted_per_percentile")
        if not per_pct:
            per_pct = {}
            streams[stream_name]["fitted_per_percentile"] = per_pct
        per_pct["p50"] = {
            k: v for k, v in fitted.items() if k != "smoothed_rate"
        }

        # And refresh the matching per-percentile EUR scalar so the
        # metadata sheet's fitted_eur_per_1000ft row uses the override
        # rather than the stale auto-fit EUR.
        eur_map = streams[stream_name].get("fitted_eur_per_unit")
        if not eur_map:
            eur_map = {}
            streams[stream_name]["fitted_eur_per_unit"] = eur_map
        eur_map["p50"] = fitted["eur_per_unit"]
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
        # JSONB mutation gotcha: `dict(row.series or {})` is a SHALLOW
        # copy — its nested `streams` dict is shared with row.series.
        # _apply_fit_overrides mutates the per-stream `fitted` dict, so
        # the original gets mutated too via that shared reference. Then
        # at flush time SQLAlchemy compares the loaded snapshot (now
        # carrying the same mutated content) to the assigned new value
        # (same content) and silently skips the UPDATE. session.commit()
        # then expires attributes, the next read pulls the still-old
        # value from the DB, and the response serializes the stale fit.
        # That's the "Di reverts to 57 after Update Fit" symptom.
        # deepcopy isolates the mutation; flag_modified is belt-and-
        # suspenders so we don't depend on SQLAlchemy's value-compare.
        updated_series = _apply_fit_overrides(
            copy.deepcopy(row.series or {}), req.fit_overrides
        )
        row.series = updated_series
        flag_modified(row, "series")
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


# Keys we surface in the metadata sheet's params block. Order is the
# downstream tool's evaluation order: ramp-prefix params first (qo /
# peak_index), then Arps decline (qi / Di / b / Df). Anything else in
# the persisted fit dict (eur_per_unit, r2, smoothed_rate) is either a
# derived value already in another block or noise.
_FITTED_PARAM_KEYS: tuple[str, ...] = (
    "model_type",
    "qi",
    "Di",
    "b",
    "Df",
    "qo",
    "peak_index",
)


def _fitted_p50_params(
    stream_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the published P50 fit's raw Arps params for one stream.

    Prefers ``fitted_per_percentile.p50`` (the source the chart's P50
    band reads from and the source we now keep in sync with the
    TweakPanel override). Falls back to ``fitted`` for the few old saves
    that predate ``fitted_per_percentile`` — those won't have the per-
    percentile dict populated, but ``fitted`` always exists when the
    auto-fit succeeded. Returns None for streams that failed to fit at
    all (no params to publish).
    """
    if not stream_data:
        return None
    per_pct = (stream_data.get("fitted_per_percentile") or {}).get("p50")
    fit = per_pct or stream_data.get("fitted")
    if not fit:
        return None
    return {k: fit.get(k) for k in _FITTED_PARAM_KEYS}


def _fitted_eur_per_1000ft(
    stream_data: dict[str, Any],
) -> dict[str, float | None]:
    """50-yr trapezoid integral of each percentile's fitted Arps rate.

    Mirrors the frontend's ``eurFromArpsParams`` helper so the export
    metadata sheet reports the same number the Type Curve UI's EUR
    table shows. Raw integral — no economic-limit cutoff (this tool is
    technical-only; economics happens downstream).

    None for any percentile whose underlying series couldn't be fit.
    """
    rates_by_pct = _evaluate_fitted_rates(stream_data)
    out: dict[str, float | None] = {}
    for key, rates in rates_by_pct.items():
        if rates is None:
            out[key] = None
            continue
        cum = 0.0
        for r in rates:
            if r is not None and math.isfinite(r):
                cum += float(r) * _DAYS_PER_MONTH
        out[key] = cum
    return out


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


# DB enum value is "per_lateral_ft" for historical reasons; the math in
# aggregate.py divides by lateral_ft / 1000, so the published rates and
# EUR are actually per 1,000 lateral ft. Humanize at the export boundary
# so the metadata field matches the chart axes and the EUR table header.
_NORMALIZATION_LABEL: dict[str, str] = {
    "per_lateral_ft": "per_1000_lateral_ft",
    "per_proppant_lb": "per_million_proppant_lb",
    "per_well": "per_well",
}


def _metadata_csv(tc: TypeCurve) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["field", "value"])
    writer.writerow(["id", str(tc.id)])
    writer.writerow(["name", tc.name])
    writer.writerow(["notes", tc.notes or ""])
    writer.writerow([
        "normalization_basis",
        _NORMALIZATION_LABEL.get(
            tc.normalization_basis.value, tc.normalization_basis.value
        ),
    ])
    writer.writerow(["alignment_method", tc.alignment_method.value])
    writer.writerow(["created_at", tc.created_at.isoformat()])
    writer.writerow(["version_of", str(tc.version_of) if tc.version_of else ""])
    writer.writerow(["n_wells", len(tc.included_api14s or [])])
    writer.writerow([])
    writer.writerow(["filter_spec_key", "filter_spec_value"])
    for k, v in (tc.filter_spec or {}).items():
        writer.writerow([k, v])
    writer.writerow([])
    # 50-yr Arps projection per percentile — what the downstream
    # economics tool actually wants. Computed at export time from the
    # persisted fitted_per_percentile params (no econ cutoff). The
    # previous block reported the data-window cumsum
    # (`implied_eur_per_1000ft`), which is a look-back QC artifact that
    # gets mistaken for an EUR; dropped on purpose.
    writer.writerow(["fitted_eur_per_1000ft (50-yr Arps projection)"])
    writer.writerow(["stream", *PERCENTILE_KEYS, "mean"])
    streams = (tc.series or {}).get("streams", {})
    for s_name in ("oil", "gas", "water"):
        s = streams.get(s_name, {})
        eur = _fitted_eur_per_1000ft(s)
        writer.writerow([
            s_name,
            *[
                f"{eur[k]:.1f}" if eur.get(k) is not None else ""
                for k in PERCENTILE_KEYS
            ],
            f"{eur['mean']:.1f}" if eur.get("mean") is not None else "",
        ])
    writer.writerow([])

    # Raw P50 Arps params per stream so the downstream econ tool can
    # re-evaluate at its own time grid (quarterly cash, daily first-year
    # build-up, etc.) or apply its own cutoff. Units: qi/qo in BOPD or
    # MCFD per 1000 ft (matches normalization_basis above); Di/Df in
    # per-year nominal; b dimensionless; peak_index in months.
    writer.writerow(["fitted_p50_params (per stream)"])
    writer.writerow(["stream", *_FITTED_PARAM_KEYS])
    for s_name in ("oil", "gas", "water"):
        s = streams.get(s_name, {})
        params = _fitted_p50_params(s)
        if params is None:
            writer.writerow([s_name, *["" for _ in _FITTED_PARAM_KEYS]])
            continue
        writer.writerow([
            s_name,
            *[
                "" if params.get(k) is None
                else params[k] if isinstance(params[k], (str, int))
                else f"{float(params[k]):.6f}"
                for k in _FITTED_PARAM_KEYS
            ],
        ])
    writer.writerow([])

    writer.writerow(["included_api14s"])
    for a in tc.included_api14s or []:
        writer.writerow([a])
    return buf.getvalue().encode()


@router.get("/{type_curve_id}/well-stats", response_model=list[TypeCurveWellStat])
def get_type_curve_well_stats(
    type_curve_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[TypeCurveWellStat]:
    """Per-well oil EUR + lateral_ft for the wells in this curve.

    Drives the slide-export probit chart. Single join: wells ⨝ forecasts
    (oil stream) filtered by api14 ∈ included_api14s. Wells missing an
    oil forecast or a lateral_ft are still returned, with nulls for the
    fields they can't supply, so the frontend can report a well count
    that matches included_api14s exactly.

    EUR is recomputed on the fly from ``forecasts.params`` using the
    same monthly-trapezoid sum the Review tab's
    ``eurFromArpsParams`` uses: evaluate the modified-hyperbolic fit
    out to 600 monthly samples and sum ``rate * DAYS_PER_MONTH``. The
    persisted ``forecasts.eur`` column was written via
    ``scipy.integrate.quad`` (continuous integral), which gives a
    ~3% lower number than the discrete monthly sum on typical Permian
    unconventionals — reading it directly puts the per-well dots on the
    probit systematically below the Review summary's mean and would
    make the slide and the Review tab disagree. Falls back to the
    stored value when params are missing or non-finite (old forecasts
    that predate the params column population).
    """
    from app.type_curves.fit_p50 import evaluate_fit

    # Same constant the frontend uses (frontend/src/forecasts/arps.ts).
    DAYS_PER_MONTH = 30.4375
    N_MONTHS = 600

    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")
    api14s = list(tc.included_api14s or [])
    if not api14s:
        return []
    rows = session.execute(
        select(
            Well.api14,
            Well.name,
            Well.lateral_ft,
            Forecast.eur,
            Forecast.model_type,
            Forecast.params,
        )
        .outerjoin(
            Forecast,
            (Forecast.api14 == Well.api14) & (Forecast.stream == Stream.OIL),
        )
        .where(Well.api14.in_(api14s))
    ).all()
    out: list[TypeCurveWellStat] = []
    for r in rows:
        lat = float(r.lateral_ft) if r.lateral_ft is not None else None
        # Recompute via monthly-trapezoid sum so the probit's per-well
        # values match what the Review tab + EUR-table cells display.
        eur: float | None = None
        if r.params:
            required = ("qi", "Di", "b", "Df")
            if all(
                k in r.params
                and isinstance(r.params[k], (int, float))
                and math.isfinite(float(r.params[k]))
                for k in required
            ):
                try:
                    fit = evaluate_fit(
                        qi=float(r.params["qi"]),
                        Di=float(r.params["Di"]),
                        b=float(r.params["b"]),
                        Df=float(r.params["Df"]),
                        # Per-well forecasts start at peak — no ramp prefix.
                        qo=float(r.params["qi"]),
                        peak_index=0,
                        n_months=N_MONTHS,
                    )
                    eur = sum(
                        rate * DAYS_PER_MONTH for rate in fit["smoothed_rate"]
                    )
                except Exception:
                    eur = None
        if eur is None and r.eur is not None:
            # Fallback: older forecasts without a populated params dict.
            eur = float(r.eur)
        per_ft = eur / lat if (eur is not None and lat and lat > 0) else None
        out.append(
            TypeCurveWellStat(
                api14=r.api14,
                name=r.name,
                lateral_ft=lat,
                oil_eur=eur,
                oil_eur_per_ft=per_ft,
            )
        )
    return out


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


# ============================ PPTX export ============================

# Office Open XML presentation MIME type. Mirrors the XLSX_MEDIA_TYPE
# constant in app/api/deals.py.
PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)


@router.post("/{type_curve_id}/export.pptx")
async def export_type_curve_pptx(
    type_curve_id: uuid.UUID,
    compare_with: uuid.UUID | None = Query(default=None, alias="compareWith"),
    rate_oil: UploadFile = File(...),
    cum_oil: UploadFile = File(...),
    rate_gas: UploadFile = File(...),
    cum_gas: UploadFile = File(...),
    rate_water: UploadFile = File(...),
    cum_water: UploadFile = File(...),
    map_image: UploadFile = File(...),
    probit_oil: UploadFile | None = File(default=None),
    probit_gas: UploadFile | None = File(default=None),
    probit_water: UploadFile | None = File(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """Build a 4-slide .pptx for the given saved type curve.

    Slide order: Oil, Gas, Water, Type Curve Wells. Each stream slide
    gets its own rate + cum chart PNG; the map is shared across all
    three. Charts are placed at fixed inch dimensions (5.42" × 2.16"
    for the charts, ~6.93" × 4.34" for the map), avoiding the
    aspect-survives-picture-frame problem of a single composite.

    ``compareWith`` is the optional comparison curve id; when set the
    param table on each stream slide gets a second row and the chart
    images carry a dotted-gray "Previous TC" overlay rendered
    client-side.
    """
    from app.exports.pptx_builder import build_deal_slide_pptx

    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="type curve not found")
    if compare_with is not None and session.get(TypeCurve, compare_with) is None:
        raise HTTPException(status_code=404, detail="comparison curve not found")

    rate_oil_bytes = await rate_oil.read()
    cum_oil_bytes = await cum_oil.read()
    rate_gas_bytes = await rate_gas.read()
    cum_gas_bytes = await cum_gas.read()
    rate_water_bytes = await rate_water.read()
    cum_water_bytes = await cum_water.read()
    map_bytes = await map_image.read()
    all_imgs = [
        rate_oil_bytes, cum_oil_bytes,
        rate_gas_bytes, cum_gas_bytes,
        rate_water_bytes, cum_water_bytes,
        map_bytes,
    ]
    if not all(all_imgs):
        raise HTTPException(status_code=400, detail="one or more panel images empty")

    # Probit uploads are optional. If ANY are provided we require ALL
    # three (one per stream slide) — partial probit would look weird.
    probit_pngs: dict[str, bytes] | None = None
    if probit_oil is not None or probit_gas is not None or probit_water is not None:
        po = await probit_oil.read() if probit_oil else b""
        pg = await probit_gas.read() if probit_gas else b""
        pw = await probit_water.read() if probit_water else b""
        if not (po and pg and pw):
            raise HTTPException(
                status_code=400,
                detail="probit images must be supplied for all three streams or none",
            )
        probit_pngs = {"oil": po, "gas": pg, "water": pw}

    try:
        content = build_deal_slide_pptx(
            session, type_curve_id, compare_with,
            stream_pngs={
                "oil": (rate_oil_bytes, cum_oil_bytes),
                "gas": (rate_gas_bytes, cum_gas_bytes),
                "water": (rate_water_bytes, cum_water_bytes),
            },
            map_png=map_bytes,
            probit_pngs=probit_pngs,
        )
    except ValueError as e:
        # Template-shape mismatch or curve-not-found shouldn't surface
        # as a 500. The builder raises ValueError for both cases.
        raise HTTPException(status_code=400, detail=str(e)) from e

    safe_name = "".join(c if c.isalnum() else "_" for c in tc.name)[:64] or "type_curve"
    log.info(
        "type_curve_exported_pptx",
        id=str(type_curve_id),
        compare_with=str(compare_with) if compare_with else None,
        n_wells=len(tc.included_api14s or []),
        bytes=len(content),
    )
    return Response(
        content=content,
        media_type=PPTX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.pptx"',
        },
    )
