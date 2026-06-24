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
from datetime import date, datetime
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
from app.type_curves.loader import (
    cohort_ramp_anchors,
    load_well_series,
    load_wells_with_forecast,
)

# Forecast-aggregation horizon. 50 years × 12 months. Picks up the same
# horizon the per-well fit_p50_series + frontend FULL_FORECAST_N_MONTHS
# use so the bands line up with the per-percentile fit downstream.
_FORECAST_N_MONTHS = 600

router = APIRouter(prefix="/type-curves", tags=["type_curves"])
log = get_logger("api.type_curves")


# ============================ schemas ============================


class ComputeRequest(BaseModel):
    api10s: list[str] = Field(min_length=1, max_length=500)
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
    included_api10s: list[str] = Field(min_length=1, max_length=500)
    normalization_basis: str = "per_lateral_ft"
    alignment_method: str = "first_prod_month"
    n_months: int | None = Field(default=None, ge=1, le=240)
    # Optional per-stream tweaked fits. When present, the computed series'
    # streams[<stream>].fitted is replaced with a re-evaluation of these
    # params and marked manual_override=True. Keys: "oil" / "gas" / "water".
    fit_overrides: dict[str, FitOverride] | None = None
    # Versions-endpoint only: when true and the parent curve is assigned
    # to a deal, the new version atomically takes the parent's deal slot
    # (version gets parent.deal_id, parent is unassigned) in the same
    # transaction — so a deal export can never see both curves at once.
    # Ignored on the plain create endpoint.
    take_over_deal: bool = False


class TypeCurvePreviewRequest(BaseModel):
    """Live preview body for the manual tweak panel — no persistence."""
    qi: float
    Di: float
    b: float
    Df: float
    qo: float
    peak_index: int = Field(ge=0, le=11)
    # 720 = 60 years × 12 months. Phase 2.5 bumped the canonical
    # aggregation horizon to 600 months (50 yr) so the TweakPanel
    # previews at that length to match the saved series; the older
    # 240-month cap (20 yr) rejected 600 with a 422. Keep a little
    # headroom past 600 in case a future horizon change wants it.
    n_months: int = Field(ge=1, le=720)


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
    included_api10s: list[str]
    normalization_basis: NormalizationBasis
    alignment_method: AlignmentMethod
    series: dict[str, Any]
    created_at: datetime
    version_of: uuid.UUID | None
    deal_id: uuid.UUID | None
    # Workspace flag — true when overrides / globals / membership have
    # changed since the last aggregation. Phase 1 reads only; Phase 2
    # sets it from forecast/membership edits.
    is_stale: bool = False

    @classmethod
    def from_orm_row(cls, tc: TypeCurve) -> "TypeCurveRow":
        return cls(
            id=tc.id,
            name=tc.name,
            notes=tc.notes,
            filter_spec=tc.filter_spec or {},
            included_api10s=tc.included_api10s or [],
            normalization_basis=tc.normalization_basis,
            alignment_method=tc.alignment_method,
            series=tc.series or {},
            created_at=tc.created_at,
            version_of=tc.version_of,
            deal_id=tc.deal_id,
            is_stale=bool(tc.is_stale),
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
    is_stale: bool = False


class TypeCurveWellStat(BaseModel):
    """Per-well oil EUR + lateral, for the slide-export probit chart.

    EUR is the persisted 50-yr Arps integral on the well's oil forecast
    (forecasts.eur). EUR/ft is EUR divided by lateral_ft — undefined and
    returned null when either is missing or lateral_ft <= 0.
    """

    api10: str
    name: str | None
    lateral_ft: float | None
    oil_eur: float | None
    oil_eur_per_ft: float | None


class WorkspaceStream(BaseModel):
    """One stream's resolved forecast for a well inside a TC workspace.

    ``source`` is "override" when the payload came from
    ``type_curves.forecast_overrides``, "global" when it came from the
    well's global forecast row, "missing" when no forecast exists for
    that (well, stream) pair.

    ``payload`` is None iff source == "missing". Otherwise it carries
    the per-stream fit shape the frontend already knows how to render
    (same keys ForecastRow surfaces for a global forecast).
    """

    source: str  # "override" | "global" | "missing"
    payload: dict[str, Any] | None


class WorkspaceWell(BaseModel):
    """One row in the TC workspace's well list."""

    api10: str
    well_name: str | None
    well_operator: str | None
    well_formation: str | None
    well_lateral_ft: float | None
    well_first_prod_date: date | None  # ISO from Well.first_prod_date
    well_county: str | None
    oil: WorkspaceStream
    gas: WorkspaceStream
    water: WorkspaceStream


class OverrideWriteRequest(BaseModel):
    """Body for PUT /type-curves/{id}/overrides/{api10}/{stream}.

    Surface: the four Arps params plus the optional ramp prefix (qo,
    peak_index_months). Server fills in the rest (eur, model_type,
    di_initial, etc.) so the override payload shape matches what the
    resolver returns for a global forecast.

    Ramp params are optional for back-compat with pre-ramp overrides
    and for callers that only want to tweak Arps. When omitted, the
    saved override is pure Arps anchored at peak — which is fine for
    rows that never had a ramp; for ramp-aware fits, the modal sends
    them so the override preserves the linear-ramp prefix.
    """

    qi: float
    Di: float
    b: float
    Df: float
    qo: float | None = None
    peak_index_months: int | None = None


class MembershipPatch(BaseModel):
    """Body for PATCH /type-curves/{id}/membership.

    ``add`` and ``remove`` are processed in that order (remove wins on
    the same api10 — pathological but cheap to define explicitly).
    Empty lists are valid no-ops.
    """

    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


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
    # In-place alignment change (e.g. moving an unshared curve onto
    # peak_ramp without versioning). Sets is_stale — the saved series
    # still reflects the OLD alignment until /reaggregate rebuilds it.
    alignment_method: str | None = None


# ============================ helpers ============================


def _validate_basis(basis: str) -> AggBasis:
    if basis not in ("per_lateral_ft", "per_proppant_lb", "per_well"):
        raise HTTPException(status_code=400, detail=f"invalid normalization_basis: {basis}")
    return basis  # type: ignore[return-value]


def _validate_alignment(method: str) -> AggAlign:
    if method not in ("peak_month", "first_prod_month", "peak_ramp"):
        raise HTTPException(status_code=400, detail=f"invalid alignment_method: {method}")
    return method  # type: ignore[return-value]


def _month_col_label(alignment: str) -> str:
    """Month-axis column name for the CSV exports — tells the
    downstream consumer what month 1 means."""
    if alignment == "first_prod_month":
        return "month_since_first_prod"
    if alignment == "peak_ramp":
        # Month 1 is the start of the cohort ramp; the peak sits at the
        # cohort-median ramp length (the fitted peak_index).
        return "month_peak_aligned_ramp"
    return "month_since_peak"


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
    api10s: list[str],
    *,
    basis: str,
    alignment: str,
    n_months: int | None,
    tc: TypeCurve | None = None,
) -> dict[str, Any]:
    """Build the persisted ``series`` JSONB.

    Two parallel aggregations, both serialized into the output:

      * ``streams`` — the canonical deliverable. Each well contributes
        observed pre-peak ramp + analytic post-peak forecast (override
        → global). Aggregated out to ``_FORECAST_N_MONTHS`` so the
        bands and the fitted P50 reflect the per-well forecasts +
        overrides the engineer published. Override edits propagate
        here; this is what re-aggregation refreshes.
      * ``observed_streams`` — empirical reference. Aggregates only the
        observed ``production_monthly`` rates (no forecast tail), capped
        at the cohort's observed history (or the request's ``n_months``).
        Surfaces on the workspace as a QC overlay; not used downstream.

    ``tc`` is None on the compute-preview path and on initial save (the
    TC row doesn't exist yet → no overrides). The forecast aggregator
    resolves to globals in that case. On re-aggregation ``tc`` is the
    persisted row and overrides flow through.
    """
    validated_alignment = _validate_alignment(alignment)
    validated_basis = _validate_basis(basis)
    resolver_tc = tc if tc is not None else _empty_tc_for_resolver()

    # peak_ramp: compute the common peak index M (per stream) ONCE and
    # hand the same dict to both loaders so the forecast bands and the
    # observed QC overlay share the exact alignment.
    anchors = (
        cohort_ramp_anchors(session, resolver_tc, api10s)
        if validated_alignment == "peak_ramp"
        else None
    )

    # --- canonical forecast-based aggregation ---
    forecast_wells = load_wells_with_forecast(
        session,
        resolver_tc,
        api10s,
        alignment=validated_alignment,
        n_months=_FORECAST_N_MONTHS,
        ramp_anchors=anchors,
    )
    if not forecast_wells:
        raise HTTPException(
            status_code=400,
            detail=(
                "no wells with both forecasts and production found "
                "(first_prod_date required for first_prod_month alignment)"
            ),
        )
    agg = aggregate(
        forecast_wells,
        normalization_basis=validated_basis,
        alignment_method=validated_alignment,
        n_months=_FORECAST_N_MONTHS,
    )
    # Fit Arps to each stream's P50 + per-percentile. The cross-well P50
    # of analytic projections is already smooth, so this fit is mostly a
    # parameter-extraction step (qi/Di/b/Df) for the param table and the
    # downstream economics export. R² is near 1.0 by construction.
    from dataclasses import replace

    from app.type_curves.fit_p50 import fit_p50_series

    fitted_streams = {}
    for stream_name, stream in agg.streams.items():
        fitted = fit_p50_series(stream.p50)
        fitted_eur: dict[str, float | None] = {}
        fitted_per_pct: dict[str, dict[str, Any] | None] = {}
        for key in ("p10", "p25", "p50", "p75", "p90", "mean"):
            series = getattr(stream, key)
            r = fit_p50_series(series)
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
    payload = serialize_aggregate(agg)

    # --- empirical observed-only aggregation ---
    observed_wells = load_well_series(
        session, api10s, alignment=validated_alignment, ramp_anchors=anchors
    )
    if observed_wells:
        observed_agg = aggregate(
            observed_wells,
            normalization_basis=validated_basis,
            alignment_method=validated_alignment,
            n_months=n_months,  # honor caller's cap when set
        )
        observed_payload = serialize_aggregate(observed_agg)
        # Strip the fitted blocks — the observed bands are for QC; we
        # don't publish an Arps fit on them, and leaving the keys at
        # null would just mirror the absence of a fit.
        for s in observed_payload.get("streams", {}).values():
            s.pop("fitted", None)
            s.pop("fitted_eur_per_unit", None)
            s.pop("fitted_per_percentile", None)
        payload["observed_streams"] = observed_payload["streams"]
        payload["observed_n_months"] = observed_payload["n_months"]
        payload["observed_n_wells"] = observed_payload["n_wells"]

        # Replace the forecast streams' well_count ribbon with the
        # OBSERVED well count, zero-padded to the forecast horizon.
        # Under forecast aggregation every well contributes a value at
        # every month, so the native well_count is a flat solid block —
        # useless as a chart ribbon. The observed count (wells with
        # actual production data) is the QC signal engineers expect:
        # high in the early months, tapering as wells with short
        # histories drop off, zero past the cohort's observed window.
        forecast_n = payload.get("n_months") or _FORECAST_N_MONTHS
        for stream_name, fcst_stream in payload.get("streams", {}).items():
            obs_stream = payload["observed_streams"].get(stream_name) or {}
            obs_wc = list(obs_stream.get("well_count") or [])
            if len(obs_wc) < forecast_n:
                obs_wc = obs_wc + [0] * (forecast_n - len(obs_wc))
            else:
                obs_wc = obs_wc[:forecast_n]
            fcst_stream["well_count"] = obs_wc
    else:
        payload["observed_streams"] = {}
        payload["observed_n_months"] = 0
        payload["observed_n_wells"] = 0

    return payload


def _empty_tc_for_resolver() -> TypeCurve:
    """Stub TC carrying empty overrides — used on the compute-preview
    and initial-save paths where no TC row exists yet. Avoids leaking
    None through to the resolver."""
    tc = TypeCurve()
    tc.forecast_overrides = {}
    return tc


# ============================ compute (preview) ============================


@router.post("/compute", response_model=ComputeResponse)
def compute_type_curve(
    req: ComputeRequest, session: Session = Depends(get_session)
) -> ComputeResponse:
    payload = _compute(
        session, req.api10s,
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
        included_api10s=req.included_api10s,
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
        session, req.included_api10s,
        basis=req.normalization_basis,
        alignment=req.alignment_method,
        n_months=req.n_months,
    )
    payload = _apply_fit_overrides(payload, req.fit_overrides)
    row = _persist(session, payload=payload, req=req, version_of=None)
    log.info(
        "type_curve_saved",
        id=str(row.id),
        n_wells=len(req.included_api10s),
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
        session, req.included_api10s,
        basis=req.normalization_basis,
        alignment=req.alignment_method,
        n_months=req.n_months,
    )
    payload = _apply_fit_overrides(payload, req.fit_overrides)
    row = _persist(session, payload=payload, req=req, version_of=parent.id)
    took_over_deal = False
    if req.take_over_deal and parent.deal_id is not None:
        # Deal-slot handoff: the version replaces its parent in the
        # deal in one transaction, so an export can never include both.
        row.deal_id = parent.deal_id
        parent.deal_id = None
        session.commit()
        session.refresh(row)
        took_over_deal = True
    log.info(
        "type_curve_versioned", id=str(row.id), parent=str(parent.id),
        n_wells=len(req.included_api10s), took_over_deal=took_over_deal,
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
            n_wells=len(r.included_api10s or []),
            created_at=r.created_at,
            version_of=r.version_of,
            deal_id=r.deal_id,
            is_stale=bool(r.is_stale),
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
    if req.alignment_method is not None:
        validated = _validate_alignment(req.alignment_method)
        if AlignmentMethod(validated) != row.alignment_method:
            row.alignment_method = AlignmentMethod(validated)
            # The persisted series was aggregated under the OLD
            # alignment — flag it so the workspace banner / the
            # update-in-place flow knows a /reaggregate is required.
            row.is_stale = True
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
# export. DAYS_PER_YEAR / 12 — the SAME convention as the fit / EUR
# math (eur.DAYS_PER_YEAR = 365) and the shared display-EUR helper
# (ramp_arps.trapezoid_eur), so the exported cum column converges to
# the metadata sheet's EUR row exactly. (This used to be 30.4375 —
# 365.25/12 — while the fit math used 365/12; a 0.07% systematic drift
# between the export cums and every other surface.)
_DAYS_PER_MONTH = 365.0 / 12.0

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

    Routes through the shared ``ramp_arps.trapezoid_eur`` (the same
    rule the frontend's ``eurFromArpsParams``, the deal-deck well rows,
    and the well-stats endpoint use) so the export metadata sheet
    reports the same number the Type Curve UI's EUR table shows. Raw
    integral — no economic-limit cutoff (this tool is technical-only;
    economics happens downstream).

    None for any percentile whose underlying series couldn't be fit.
    """
    from app.forecasting.ramp_arps import trapezoid_eur

    rates_by_pct = _evaluate_fitted_rates(stream_data)
    return {
        key: (trapezoid_eur(rates) if rates is not None else None)
        for key, rates in rates_by_pct.items()
    }


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

    month_col = _month_col_label(alignment)
    buf = io.StringIO()
    writer = csv.writer(buf)
    # Columns are bare ``{pct}_{rate|cum}`` — stream is implied by the
    # filename (oil_forecast.csv / gas_forecast.csv / water_forecast.csv)
    # and these are forecast-only files (the empirical bands live in
    # `{stream}_rates.csv` with bare ``p10``/``p25``/... columns, no
    # ``_rate``/``_cum`` suffix). Matches the deal XLSX's
    # ``{stream}_{pct}_{rate|cum}`` convention so downstream econ
    # tooling can speak one schema across both exports.
    header: list[str] = [month_col]
    for key in _PERCENTILE_KEYS_WITH_MEAN:
        header.append(f"{key}_rate")
        header.append(f"{key}_cum")
    writer.writerow(header)

    # Track running cum per percentile via the trapezoid rule: month
    # K's volume is (rate at start of month + rate at end of month) / 2
    # * dt. Last month uses the same rate for both endpoints (flat
    # extrapolation). Matches the closed-form ``compute_eur`` to <0.1%
    # on Permian fits — the older right-endpoint rectangle rule had a
    # ~0.2% systematic bias. Kept in sync with the frontend's
    # ``cumulateNumericArray``.
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
            nxt = arr[i + 1] if i + 1 < len(arr) else None
            b = float(nxt) if (nxt is not None and math.isfinite(nxt)) else rate
            cum_by_pct[key] += (rate + b) / 2.0 * _DAYS_PER_MONTH
            row.append(f"{rate:.6f}")
            row.append(f"{cum_by_pct[key]:.3f}")
        writer.writerow(row)
    return buf.getvalue().encode()


def _stream_csv(
    stream_name: str, stream_data: dict[str, Any], alignment: str
) -> bytes:
    # Column name reflects the alignment so downstream consumers know
    # whether month 1 is peak month or first-prod month.
    month_col = _month_col_label(alignment)
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
    # SPE / PRMS orientation: p10 columns = HIGH case, p90 = LOW case.
    writer.writerow(
        ["percentile_convention", "SPE (P10 = high case, P90 = low case)"]
    )
    writer.writerow(["created_at", tc.created_at.isoformat()])
    writer.writerow(["version_of", str(tc.version_of) if tc.version_of else ""])
    writer.writerow(["n_wells", len(tc.included_api10s or [])])
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

    writer.writerow(["included_api10s"])
    for a in tc.included_api10s or []:
        writer.writerow([a])
    return buf.getvalue().encode()


@router.get("/{type_curve_id}/well-stats", response_model=list[TypeCurveWellStat])
def get_type_curve_well_stats(
    type_curve_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[TypeCurveWellStat]:
    """Per-well oil EUR + lateral_ft for the wells in this curve.

    Drives the slide-export probit chart. Single join: wells ⨝ forecasts
    (oil stream) filtered by api10 ∈ included_api10s. Wells missing an
    oil forecast or a lateral_ft are still returned, with nulls for the
    fields they can't supply, so the frontend can report a well count
    that matches included_api10s exactly.

    EUR is recomputed on the fly from the RESOLVED oil forecast
    (TC override → global → none) via the shared
    ``ramp_arps.display_eur_from_params`` — the same monthly-trapezoid
    convention the Review tab's ``eurFromArpsParams``, the deal-deck
    well rows, and the per-percentile export EURs use, ramp prefix
    included. Routing through ``resolve_forecast`` keeps the probit
    dots aligned with what the workspace shows for each well — a TC
    override edited via the workspace flows through to the deck.
    Falls back to the stored ``forecasts.eur`` column when params are
    missing or non-finite (old forecasts that predate the params
    column population). The persisted ``forecasts.eur`` was written
    via ``scipy.integrate.quad`` (continuous integral); the trapezoid
    agrees with it to <0.1% on Permian fits.
    """
    from app.forecasting.ramp_arps import display_eur_from_params
    from app.type_curves.overrides import resolve_forecast

    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")
    api10s = list(tc.included_api10s or [])
    if not api10s:
        return []

    well_rows = session.execute(
        select(Well.api10, Well.name, Well.lateral_ft).where(
            Well.api10.in_(api10s)
        )
    ).all()
    well_by_api10 = {r.api10: r for r in well_rows}

    # Pull only oil forecasts — probit is oil-only today.
    forecast_rows = session.execute(
        select(Forecast)
        .where(Forecast.api10.in_(api10s))
        .where(Forecast.stream == Stream.OIL)
    ).scalars().all()
    forecast_by_api10 = {f.api10: f for f in forecast_rows}

    out: list[TypeCurveWellStat] = []
    for api10 in api10s:
        wrow = well_by_api10.get(api10)
        lat = (
            float(wrow.lateral_ft)
            if wrow and wrow.lateral_ft is not None
            else None
        )
        name = wrow.name if wrow else None

        resolved = resolve_forecast(
            tc, api10, Stream.OIL, forecast_by_api10.get(api10)
        )
        payload = resolved.payload
        eur: float | None = None

        if payload:
            params = payload.get("params") or {}
            try:
                # Ramp-aware: post-migration-0013 fits carry qo /
                # peak_index_months in params and the stored eur
                # includes the ramp volume — dropping the ramp here
                # (the old qo=qi / peak_index=0 shortcut) understated
                # ramp-fit wells by the ramp volume vs every other
                # surface.
                eur = display_eur_from_params(params)
            except Exception:
                eur = None
            if eur is None:
                # Last-ditch fallback: stored eur on the payload (override
                # payloads carry it, global rows project it through).
                stored = payload.get("eur")
                if stored is not None and math.isfinite(float(stored)):
                    eur = float(stored)

        per_ft = eur / lat if (eur is not None and lat and lat > 0) else None
        out.append(
            TypeCurveWellStat(
                api10=api10,
                name=name,
                lateral_ft=lat,
                oil_eur=eur,
                oil_eur_per_ft=per_ft,
            )
        )
    return out


@router.get("/{type_curve_id}/wells", response_model=list[WorkspaceWell])
def get_type_curve_workspace_wells(
    type_curve_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[WorkspaceWell]:
    """Per-well resolved forecasts for the TC workspace (Phase 1, read-only).

    Returns one row per ``included_api10`` with each well's joined
    attributes and a resolved forecast per stream:

      * ``oil`` / ``gas`` / ``water`` → {source, payload}
      * source = "override" when ``tc.forecast_overrides`` has an entry
        for (api10, stream), "global" when it falls through to the
        forecasts table, "missing" when no forecast exists at all.

    All resolution goes through ``resolve_forecast`` so Phase 2 (which
    will write overrides on edit) shares the same fallback rule. The
    payload shape mirrors what the frontend already reads for a global
    forecast — no special-casing in the UI.
    """
    from app.type_curves.overrides import resolve_forecast

    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")
    api10s = list(tc.included_api10s or [])
    if not api10s:
        return []

    # One round-trip: wells joined to their forecasts (any stream). We
    # group in Python rather than per-stream queries — the per-well
    # count is small (~10-30) and avoiding the N+1 keeps the endpoint
    # snappy on the workspace page load.
    well_rows = session.execute(
        select(
            Well.api10,
            Well.name,
            Well.operator,
            Well.formation_blueox.label("formation"),
            Well.lateral_ft,
            Well.first_prod_date,
            Well.county,
        ).where(Well.api10.in_(api10s))
    ).all()
    wells_by_api10 = {r.api10: r for r in well_rows}

    forecast_rows = session.execute(
        select(Forecast).where(Forecast.api10.in_(api10s))
    ).scalars().all()
    forecasts_by_well: dict[str, dict[Stream, Forecast]] = {}
    for f in forecast_rows:
        forecasts_by_well.setdefault(f.api10, {})[f.stream] = f

    out: list[WorkspaceWell] = []
    for api10 in api10s:
        well = wells_by_api10.get(api10)
        well_forecasts = forecasts_by_well.get(api10, {})

        def _stream(s: Stream) -> WorkspaceStream:
            resolved = resolve_forecast(tc, api10, s, well_forecasts.get(s))
            return WorkspaceStream(source=resolved.source, payload=resolved.payload)

        out.append(
            WorkspaceWell(
                api10=api10,
                well_name=well.name if well else None,
                well_operator=well.operator if well else None,
                well_formation=well.formation if well else None,
                well_lateral_ft=(
                    float(well.lateral_ft)
                    if well and well.lateral_ft is not None
                    else None
                ),
                well_first_prod_date=well.first_prod_date if well else None,
                well_county=well.county if well else None,
                oil=_stream(Stream.OIL),
                gas=_stream(Stream.GAS),
                water=_stream(Stream.WATER),
            )
        )
    return out


# ============================ Phase 2: workspace edits ============================
# Per-TC forecast overrides + membership edits + lazy re-aggregation.
#
# Architectural reminder (correctness-critical): the canonical bands +
# fitted P50 come from forecast-based aggregation (Phase 2.5 / Option B):
# each well contributes observed pre-peak ramp + analytic post-peak
# forecast (override → global) out to 50 years. So per-well overrides
# DO change the bands and the fitted P50 — that's why override writes
# flip is_stale. Re-aggregation rebuilds the panel using current
# overrides + membership.
#
# A second observed-only aggregation lives in series.observed_streams
# as a workspace QC overlay. The workspace + Type Curve tab read from
# series.streams (forecast-based); the empirical overlay reads from
# series.observed_streams.
# ==================================================================


def _resolve_stream(value: str) -> Stream:
    """Coerce a path-param string ('oil'/'gas'/'water') into the enum
    and 404 on anything else. Keeps the endpoint signatures readable."""
    try:
        return Stream(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown stream: {value}"
        ) from exc


def _override_payload_from_params(
    *,
    qi: float,
    Di: float,
    b: float,
    Df: float,
    qo: float | None = None,
    peak_index_months: int | None = None,
) -> dict[str, Any]:
    """Project a (qi, Di, b, Df, qo?, peak_index_months?) tweak into
    the workspace-payload shape.

    Mirrors the structure ``ResolvedForecast.payload`` carries when
    sourced from a global forecast row — so override and global look
    interchangeable to the workspace renderer and to Phase 3's
    economics export. EUR uses ``compute_total_eur`` (ramp + Arps over
    50 yr) when the ramp prefix is present, falling back to plain
    ``compute_eur`` otherwise. No economic-limit cutoff (this tool
    is technical-only — see memory note).
    """
    from app.forecasting.eur import compute_eur
    from app.forecasting.metrics import effective_decline_first_year
    from app.forecasting.ramp_arps import compute_total_eur

    params: dict[str, float | int] = {"qi": qi, "Di": Di, "b": b, "Df": Df}
    if qo is not None and peak_index_months is not None and peak_index_months > 0:
        params["qo"] = qo
        params["peak_index_months"] = int(peak_index_months)
        eur = compute_total_eur(
            model_type="modified_hyperbolic", params=params, economic_limit=0.0
        )
    else:
        eur = compute_eur("modified_hyperbolic", params, economic_limit=0.0)
    di_eff = effective_decline_first_year(Di, b)
    return {
        "params": params,
        "model_type": "modified_hyperbolic",
        "qi": qi,
        "di_initial": Di,
        "di_effective": di_eff,
        "b": b,
        "df_terminal": Df,
        "qo": qo,
        "peak_index_months": (
            int(peak_index_months) if peak_index_months is not None else None
        ),
        "eur": eur,
        "peak_rate": None,
        "peak_month_date": None,
        "fit_method": "rate_cum",
        "fit_r2": None,
        "fit_rmse": None,
        "downtime_ratio": None,
        "manual_override": True,
        "locked": False,
    }


@router.put(
    "/{type_curve_id}/overrides/{api10}/{stream}",
    response_model=WorkspaceStream,
)
def put_forecast_override(
    type_curve_id: uuid.UUID,
    api10: str,
    stream: str,
    body: OverrideWriteRequest,
    session: Session = Depends(get_session),
) -> WorkspaceStream:
    """Create or update a TC-scoped forecast override for one (well, stream).

    Does NOT mark the curve stale — see module header for why
    overrides don't affect the aggregated bands. Returns the resolved
    stream (source="override") so the frontend can refresh in place.
    """
    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")
    stream_enum = _resolve_stream(stream)
    if api10 not in (tc.included_api10s or []):
        raise HTTPException(
            status_code=400,
            detail=f"api10 {api10} is not a member of this type curve",
        )

    payload = _override_payload_from_params(
        qi=body.qi, Di=body.Di, b=body.b, Df=body.Df,
        qo=body.qo, peak_index_months=body.peak_index_months,
    )
    overrides = dict(tc.forecast_overrides or {})
    well_block = dict(overrides.get(api10) or {})
    well_block[stream_enum.value] = payload
    overrides[api10] = well_block
    tc.forecast_overrides = overrides
    tc.is_stale = True  # forecast-aggregation: overrides DO change the bands
    flag_modified(tc, "forecast_overrides")
    session.commit()
    log.info(
        "override_written",
        type_curve_id=str(type_curve_id),
        api10=api10,
        stream=stream_enum.value,
    )
    return WorkspaceStream(source="override", payload=payload)


@router.delete(
    "/{type_curve_id}/overrides/{api10}/{stream}",
    response_model=WorkspaceStream,
)
def delete_forecast_override(
    type_curve_id: uuid.UUID,
    api10: str,
    stream: str,
    session: Session = Depends(get_session),
) -> WorkspaceStream:
    """Revert a TC override back to the global forecast.

    No-op-safe: if no override exists for this (well, stream), returns
    the resolved global (or "missing") without complaint. Does NOT
    mark stale.
    """
    from app.type_curves.overrides import resolve_forecast

    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")
    stream_enum = _resolve_stream(stream)

    overrides = dict(tc.forecast_overrides or {})
    well_block = dict(overrides.get(api10) or {})
    if stream_enum.value in well_block:
        del well_block[stream_enum.value]
        if well_block:
            overrides[api10] = well_block
        else:
            overrides.pop(api10, None)
        tc.forecast_overrides = overrides
        tc.is_stale = True  # reverting an override changes the bands too
        flag_modified(tc, "forecast_overrides")
        session.commit()
        log.info(
            "override_deleted",
            type_curve_id=str(type_curve_id),
            api10=api10,
            stream=stream_enum.value,
        )

    # Return the now-resolved stream so the frontend can swap the
    # modal / workspace row to global without a second round-trip.
    global_f = session.execute(
        select(Forecast)
        .where(Forecast.api10 == api10)
        .where(Forecast.stream == stream_enum)
    ).scalar_one_or_none()
    resolved = resolve_forecast(tc, api10, stream_enum, global_f)
    return WorkspaceStream(source=resolved.source, payload=resolved.payload)


@router.patch(
    "/{type_curve_id}/membership", response_model=TypeCurveRow
)
def patch_type_curve_membership(
    type_curve_id: uuid.UUID,
    body: MembershipPatch,
    session: Session = Depends(get_session),
) -> TypeCurveRow:
    """Mutate ``included_api10s`` (add / remove) and mark the curve stale.

    Validates that every api10 in ``add`` exists in the wells table —
    silently dropping unknown api10s here would create a curve whose
    workspace renders empty rows for ghost members. ``remove`` is
    forgiving of unknown api10s (just a set difference). Overrides
    for removed wells are cleaned up to keep the JSONB tidy.
    """
    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")

    if body.add:
        known = set(
            session.execute(
                select(Well.api10).where(Well.api10.in_(body.add))
            ).scalars()
        )
        unknown = [a for a in body.add if a not in known]
        if unknown:
            raise HTTPException(
                status_code=400, detail=f"unknown api10s: {unknown}"
            )

    members = list(tc.included_api10s or [])
    if body.add:
        for a in body.add:
            if a not in members:
                members.append(a)
    if body.remove:
        remove_set = set(body.remove)
        members = [a for a in members if a not in remove_set]
        # Tidy: drop overrides for removed wells so the JSONB doesn't
        # accumulate orphan entries that the resolver would never read.
        if tc.forecast_overrides:
            overrides = {
                k: v for k, v in tc.forecast_overrides.items() if k not in remove_set
            }
            if overrides != tc.forecast_overrides:
                tc.forecast_overrides = overrides
                flag_modified(tc, "forecast_overrides")

    if members != (tc.included_api10s or []):
        tc.included_api10s = members
        tc.is_stale = True
        flag_modified(tc, "included_api10s")

    session.commit()
    session.refresh(tc)
    log.info(
        "membership_patched",
        type_curve_id=str(type_curve_id),
        added=body.add,
        removed=body.remove,
        n_wells=len(members),
    )
    return TypeCurveRow.from_orm_row(tc)


@router.post(
    "/{type_curve_id}/reaggregate", response_model=TypeCurveRow
)
def reaggregate_type_curve(
    type_curve_id: uuid.UUID, session: Session = Depends(get_session)
) -> TypeCurveRow:
    """Re-run aggregation + P50 fitter with current ``included_api10s``.

    Overwrites ``series`` in place; clears ``is_stale``. Honors the
    saved curve's normalization basis + alignment method + n_months
    so the recomputed bands sit on the same axes as the original.

    Failures (no eligible wells, fit doesn't converge) leave
    ``series`` and ``is_stale`` unchanged so the user keeps the prior
    deliverable until they fix the input.
    """
    tc = session.get(TypeCurve, type_curve_id)
    if tc is None:
        raise HTTPException(status_code=404, detail="not found")
    api10s = list(tc.included_api10s or [])
    if not api10s:
        raise HTTPException(
            status_code=400,
            detail="cannot re-aggregate: type curve has no included wells",
        )

    # n_months on the persisted series carries the original aggregation
    # window. None means "use the full history available for these
    # wells" — same default as compute_type_curve. Under the forecast-
    # aggregation model the canonical streams always run to
    # _FORECAST_N_MONTHS regardless; this n_months caps only the
    # observed_streams block.
    n_months = (tc.series or {}).get("observed_n_months") or (tc.series or {}).get("n_months")

    payload = _compute(
        session,
        api10s,
        basis=tc.normalization_basis.value,
        alignment=tc.alignment_method.value,
        n_months=n_months,
        tc=tc,
    )
    tc.series = payload
    tc.is_stale = False
    flag_modified(tc, "series")
    session.commit()
    session.refresh(tc)
    log.info(
        "type_curve_reaggregated",
        type_curve_id=str(type_curve_id),
        n_wells=len(api10s),
    )
    return TypeCurveRow.from_orm_row(tc)


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
        n_wells=len(tc.included_api10s or []),
        bytes=len(content),
    )
    return Response(
        content=content,
        media_type=PPTX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.pptx"',
        },
    )
