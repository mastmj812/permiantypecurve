// Type curve API client. Mirrors app/api/type_curves.py.

import { apiFetch } from "./auth";
import type {
  BuildupPreview,
  ExclusionEntry,
  FilterSpec,
  GeoJsonPolygon,
  ProvenanceDraft,
} from "./types";

// Wire shape of SaveRequest.provenance (ProvenanceIn server-side).
// filter_snapshot is NOT here — it rides SaveRequest.filter_spec and
// the server copies it into the stored provenance (single source).
export interface ProvenanceBody {
  selection_events: ProvenanceDraft["selection_events"];
  partition: ProvenanceDraft["partition"];
  exclusions: Record<string, ExclusionEntry>;
  // v2 (both optional server-side; always sent by current clients):
  manual_exclusions: Record<string, ExclusionEntry>;
  formations: string[] | null;
}

// Live build-up preview for the Map tab's Buildup drawer. Stateless —
// the server recomputes the universe from AOI ∩ formations and runs the
// same waterfall engine the export sheet uses.
export async function fetchBuildupPreview(args: {
  aoi_polygons: GeoJsonPolygon[];
  formations: string[];
  filter_spec: FilterSpec;
  cohort_api10s: string[];
  manual_exclusions: Record<string, ExclusionEntry>;
}): Promise<BuildupPreview> {
  const r = await apiFetch("/api/type-curves/buildup/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!r.ok) throw new Error(`buildup preview failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as BuildupPreview;
}

export type NormalizationBasis = "per_lateral_ft" | "per_proppant_lb" | "per_well";

// Geologic risking scalars per stream; absent key = 1.0 (unrisked).
// The API always serves the CLEAN series — display code applies these
// via src/typeCurves/risking.ts (see that module for why).
export type RiskMultipliers = Partial<Record<"oil" | "gas" | "water", number>>;
// peak_ramp = peak-aligned with ramp lookback: every well's peak sits
// at the cohort-median ramp length, its own ramp in the months before.
export type AlignmentMethod = "peak_month" | "first_prod_month" | "peak_ramp";

// Per-stream forecast mode for the PUBLISHED fitted curve. gas/water
// only — oil is always an independent Arps fit. "ratio" = the stream is
// a fitted ratio of cumulative oil x the TC oil stream (backend
// app/type_curves/ratio_mode.py). Never inferred: the engineer picks.
export type StreamMode = "arps" | "ratio";

export interface FittedTypeCurve {
  model_type: string;
  // RATIO-MODE CAVEAT: when `mode === "ratio"` the Arps fields below
  // (qi/Di/b/Df, qo/peak_index/ramp_eur/arps_eur, r2 as Arps-R²) are
  // ABSENT at runtime — the type keeps them required for the common
  // Arps shape; check `mode` before reading them. eur_per_unit and
  // smoothed_rate are always present in both shapes.
  qi: number;
  Di: number;          // nominal per-year
  b: number;
  Df: number;
  eur_per_unit: number; // BBL or MCF per 1,000 lateral ft (total: ramp + Arps)
  r2?: number | null;   // auto-fit R² (Arps: cum fit; ratio: ln-ratio fit)
  rmse?: number;        // Arps auto-fit only
  smoothed_rate: number[];
  // ---- ratio-mode fields (present iff mode === "ratio") ----
  mode?: StreamMode;
  alpha?: number;              // ln r at Np = 0
  beta?: number;               // d(ln r)/d(Np), per BBL-per-unit of cum oil
  sub_mode?: "exp_cum" | "constant";
  n_months?: number;           // valid months in the ratio regression
  r_const?: number | null;     // constant-fallback ratio (median of tail)
  implied_effective_decline_yr1?: number | null; // from the DERIVED series
  oil_ref?: Record<string, number | null>;       // oil fit snapshot (audit)
  // Ramp-prefix params — initial rate, ramp length, and the ramp's EUR
  // contribution. peak_index=0 + qo=qi reduces to pure post-peak Arps
  // (back-compat for type curves built before the ramp model).
  qo?: number;
  peak_index?: number;
  ramp_eur?: number;
  arps_eur?: number;
  // True when the fit was supplied by the manual-tweak panel rather
  // than auto-fit. Stored on save and surfaced in the UI as a badge.
  manual_override?: boolean;
}

export interface FitOverride {
  qi: number;
  Di: number;
  b: number;
  Df: number;
  qo: number;
  peak_index: number;
}

export interface TypeCurvePreviewResponse {
  smoothed_rate: number[];
  eur_per_unit: number;
  ramp_eur: number;
  arps_eur: number;
}

export interface StreamSeries {
  p10: Array<number | null>;
  p25: Array<number | null>;
  p50: Array<number | null>;
  p75: Array<number | null>;
  p90: Array<number | null>;
  mean: Array<number | null>;
  well_count: number[];
  implied_eur_per_1000ft: Record<string, number>;
  // Server-fit Arps decline on the P50. null when not enough data.
  fitted: FittedTypeCurve | null;
  // 50-yr projected EUR per percentile + mean from independent Arps
  // fits to each series. Keys: p10, p25, p50, p75, p90, mean. Per-key
  // value is null when that series couldn't be fit (too short / all-zero).
  // Older saved type curves predate this field and serialize it as null.
  fitted_eur_per_unit: Record<string, number | null> | null;
  // Full per-percentile fit parameters (qi/Di/b/Df/qo/peak_index/…).
  // Lets the frontend evaluate each percentile's Arps decline at
  // arbitrary horizons (e.g. the full-forecast charts that go out
  // to month 600). Same key set as fitted_eur_per_unit. Null on old
  // saved curves that predate this field; the full-forecast charts
  // fall back to just the P50 fitted line in that case.
  fitted_per_percentile: Record<string, FittedTypeCurve | null> | null;
}

export interface AggregatePayload {
  n_months: number;
  n_wells: number;
  normalization_basis: NormalizationBasis;
  alignment_method: AlignmentMethod;
  streams: { oil: StreamSeries; gas: StreamSeries; water: StreamSeries };
  // Phase 2.5: observed-only aggregation (empirical bands, no fits)
  // for the workspace QC overlay. Absent on curves saved before
  // Phase 2.5 — frontend hides the QC chart in that case.
  observed_streams?: {
    oil: StreamSeries;
    gas: StreamSeries;
    water: StreamSeries;
  };
  observed_n_months?: number;
  observed_n_wells?: number;
}

export interface TypeCurveSummary {
  id: string;
  name: string;
  notes: string | null;
  normalization_basis: NormalizationBasis;
  alignment_method: AlignmentMethod;
  n_wells: number;
  created_at: string;
  version_of: string | null;
  // Owning deal (1:N). Null when the curve isn't assigned to any deal.
  deal_id: string | null;
  // True when the curve's underlying overrides / globals / membership
  // have changed since the last aggregation (set by Phase 2). Phase 1
  // surfaces it; defaults false on existing curves.
  is_stale?: boolean;
  // Geologic risking; {} / absent = unrisked. Baked into every export
  // server-side; applied client-side for display.
  risk_multipliers?: RiskMultipliers;
}

export interface TypeCurveRow extends TypeCurveSummary {
  filter_spec: Record<string, unknown>;
  included_api10s: string[];
  series: AggregatePayload;
  // Build-up provenance (raw funnel inputs, server-assembled). {} =
  // not recorded (curve saved before build-up tracking). Read-only.
  provenance: Record<string, unknown>;
}

export async function computeTypeCurve(args: {
  api10s: string[];
  normalization_basis?: NormalizationBasis;
  alignment_method?: AlignmentMethod;
  n_months?: number | null;
  // Per-stream mode for the published fitted curve (gas/water only).
  // Omitted keys default to Arps server-side.
  stream_modes?: Partial<Record<"gas" | "water", StreamMode>> | null;
}): Promise<AggregatePayload> {
  const r = await apiFetch("/api/type-curves/compute", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!r.ok) throw new Error(`compute failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as AggregatePayload;
}

export async function saveTypeCurve(args: {
  name: string;
  notes?: string | null;
  filter_spec?: Record<string, unknown>;
  included_api10s: string[];
  normalization_basis?: NormalizationBasis;
  alignment_method?: AlignmentMethod;
  n_months?: number | null;
  version_of?: string | null;
  // Optional per-stream override of the fitted curve. Server re-evaluates
  // these and bakes them into the saved series.
  fit_overrides?: Record<string, FitOverride> | null;
  // Versions only: atomically move the parent's deal assignment onto
  // the new version (parent is unassigned in the same transaction).
  take_over_deal?: boolean;
  // Build-up funnel inputs (fresh-aggregate saves). Null/omitted on a
  // version save inherits the parent's provenance server-side.
  provenance?: ProvenanceBody | null;
  // Per-stream mode (gas/water only). On a version save, null/omitted
  // inherits the PARENT's modes from its saved series server-side — a
  // version can never silently convert a ratio stream back to Arps.
  stream_modes?: Partial<Record<"gas" | "water", StreamMode>> | null;
}): Promise<TypeCurveRow> {
  const path = args.version_of
    ? `/api/type-curves/${args.version_of}/versions`
    : "/api/type-curves";
  const r = await apiFetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name: args.name,
      notes: args.notes ?? null,
      filter_spec: args.filter_spec ?? {},
      included_api10s: args.included_api10s,
      normalization_basis: args.normalization_basis ?? "per_lateral_ft",
      alignment_method: args.alignment_method ?? "peak_ramp",
      n_months: args.n_months ?? null,
      fit_overrides: args.fit_overrides ?? null,
      take_over_deal: args.take_over_deal ?? false,
      provenance: args.provenance ?? null,
      stream_modes: args.stream_modes ?? null,
    }),
  });
  if (!r.ok) throw new Error(`save failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as TypeCurveRow;
}

export async function previewTypeCurveFit(args: {
  qi: number;
  Di: number;
  b: number;
  Df: number;
  qo: number;
  peak_index: number;
  n_months: number;
}): Promise<TypeCurvePreviewResponse> {
  const r = await apiFetch("/api/type-curves/preview", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!r.ok) throw new Error(`preview failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as TypeCurvePreviewResponse;
}

export async function listTypeCurves(): Promise<TypeCurveSummary[]> {
  const r = await apiFetch("/api/type-curves");
  if (!r.ok) throw new Error(`list failed: ${r.status}`);
  return (await r.json()) as TypeCurveSummary[];
}

export async function fetchTypeCurve(id: string): Promise<TypeCurveRow> {
  const r = await apiFetch(`/api/type-curves/${id}`);
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  return (await r.json()) as TypeCurveRow;
}

export async function patchTypeCurve(
  id: string,
  body: {
    name?: string;
    notes?: string | null;
    fit_overrides?: Record<string, FitOverride> | null;
    // Explicit-null-vs-omitted matters here: pass `null` to un-assign,
    // omit the key entirely to leave the current deal_id alone.
    deal_id?: string | null;
    // In-place alignment change — marks the curve stale; follow with
    // reaggregateTypeCurve to rebuild the series under it.
    alignment_method?: AlignmentMethod;
    // Geologic risking; applied at the read/export boundary, so no
    // reaggregate is needed and is_stale does NOT flip.
    risk_multipliers?: RiskMultipliers;
  },
): Promise<TypeCurveRow> {
  const r = await apiFetch(`/api/type-curves/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`patch failed: ${r.status}`);
  return (await r.json()) as TypeCurveRow;
}

export async function deleteTypeCurve(id: string): Promise<void> {
  const r = await apiFetch(`/api/type-curves/${id}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`delete failed: ${r.status}`);
}

export interface TypeCurveWellStat {
  api10: string;
  name: string | null;
  lateral_ft: number | null;
  oil_eur: number | null;
  oil_eur_per_ft: number | null;
}

export async function fetchTypeCurveWellStats(
  id: string,
): Promise<TypeCurveWellStat[]> {
  const r = await apiFetch(`/api/type-curves/${id}/well-stats`);
  if (!r.ok) throw new Error(`well-stats failed: ${r.status}`);
  return (await r.json()) as TypeCurveWellStat[];
}

// ============ Workspace endpoint (Phase 1: read-only) ============

// Per-stream resolved forecast inside a TC workspace. ``source``
// surfaces whether the payload came from an override, a global
// forecast, or "missing" (no forecast for that stream).
export interface WorkspaceStreamForecast {
  source: "override" | "global" | "missing";
  // Same shape as the per-stream payload of a global forecast row —
  // params + qi/Di/b/Df/eur/fit_r2/manual_override/locked/... — so
  // ForecastDetailModal can render it unchanged. Null iff source ===
  // "missing".
  payload: Record<string, unknown> | null;
}

export interface WorkspaceWell {
  api10: string;
  well_name: string | null;
  well_operator: string | null;
  well_formation: string | null;
  well_lateral_ft: number | null;
  well_first_prod_date: string | null;
  well_county: string | null;
  // Water-stream provenance flag (wells.water_source). FLAG ONLY —
  // drives the water badge + workspace composition line.
  well_water_source: string | null;
  well_wor_cv: number | null;
  oil: WorkspaceStreamForecast;
  gas: WorkspaceStreamForecast;
  water: WorkspaceStreamForecast;
}

export async function fetchTypeCurveWorkspaceWells(
  id: string,
): Promise<WorkspaceWell[]> {
  const r = await apiFetch(`/api/type-curves/${id}/wells`);
  if (!r.ok) throw new Error(`workspace wells failed: ${r.status}`);
  return (await r.json()) as WorkspaceWell[];
}

// ============ Phase 2: workspace edits ============
//
// Each call mutates the TC server-side. The workspace re-fetches its
// well list after every mutation rather than reconciling locally —
// keeps the override/global/missing source flags honest and avoids
// drift between the TC row's series and the per-well decoration.

// Stream type — re-exported from the forecasts API so callers can
// import everything from one place when wiring Phase 2 endpoints.
export type { Stream } from "./forecasts";

export async function putForecastOverride(
  id: string,
  api10: string,
  stream: "oil" | "gas" | "water",
  params: {
    qi: number;
    Di: number;
    b: number;
    Df: number;
    // Optional linear-ramp prefix. When the underlying global fit
    // had ramp params (the post-cutover default), passing them here
    // preserves the ramp on the override too — without them the
    // override silently degenerates to pure Arps and the chart's
    // shape changes after save.
    qo?: number | null;
    peak_index_months?: number | null;
  },
): Promise<WorkspaceStreamForecast> {
  const r = await apiFetch(
    `/api/type-curves/${id}/overrides/${api10}/${stream}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(params),
    },
  );
  if (!r.ok) throw new Error(`override write failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as WorkspaceStreamForecast;
}

export async function deleteForecastOverride(
  id: string,
  api10: string,
  stream: "oil" | "gas" | "water",
): Promise<WorkspaceStreamForecast> {
  const r = await apiFetch(
    `/api/type-curves/${id}/overrides/${api10}/${stream}`,
    { method: "DELETE" },
  );
  if (!r.ok) throw new Error(`override delete failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as WorkspaceStreamForecast;
}

export async function patchTypeCurveMembership(
  id: string,
  body: {
    add?: string[];
    remove?: string[];
    // Build-up reason for the removal (one entry covers the remove
    // list — the UI removes one well at a time). The API tolerates
    // absence (records "other"); the UI always sends one.
    remove_reason?: ExclusionEntry | null;
  },
): Promise<TypeCurveRow> {
  const r = await apiFetch(`/api/type-curves/${id}/membership`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      add: body.add ?? [],
      remove: body.remove ?? [],
      remove_reason: body.remove_reason ?? null,
    }),
  });
  if (!r.ok) throw new Error(`membership patch failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as TypeCurveRow;
}

export async function reaggregateTypeCurve(id: string): Promise<TypeCurveRow> {
  const r = await apiFetch(`/api/type-curves/${id}/reaggregate`, {
    method: "POST",
  });
  if (!r.ok) throw new Error(`reaggregate failed: ${r.status} ${await r.text()}`);
  return (await r.json()) as TypeCurveRow;
}

// CSV export — auth-aware download via blob.
// Can't use <a href download> because it won't attach the bearer header;
// we fetch the zip with apiFetch, then synthesize a blob URL the browser
// downloads under the desired filename.
export async function downloadTypeCurveExport(
  id: string,
  filename = "type_curve.zip",
): Promise<void> {
  const r = await apiFetch(`/api/type-curves/${id}/export`);
  if (!r.ok) throw new Error(`export failed: ${r.status}`);
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// PPTX export — server-side build against the brand template.
// Sends 7 PNGs: per-stream rate + cum (oil/gas/water) and a single
// shared map. Backend assembles the 4-slide deck: Oil, Gas, Water,
// Type Curve Wells. Each chart goes onto its slide as a separate
// picture at fixed inch dimensions, so chart sizes don't depend on
// a composite aspect surviving the template's picture-frame scale.
export async function exportTypeCurvePptx(args: {
  id: string;
  compareWithId: string | null;
  panels: {
    oil: { rate: Blob; cum: Blob; probit?: Blob };
    gas: { rate: Blob; cum: Blob; probit?: Blob };
    water: { rate: Blob; cum: Blob; probit?: Blob };
    map: Blob;
  };
  filename?: string;
}): Promise<void> {
  const fd = new FormData();
  fd.append("rate_oil", args.panels.oil.rate, "rate_oil.png");
  fd.append("cum_oil", args.panels.oil.cum, "cum_oil.png");
  fd.append("rate_gas", args.panels.gas.rate, "rate_gas.png");
  fd.append("cum_gas", args.panels.gas.cum, "cum_gas.png");
  fd.append("rate_water", args.panels.water.rate, "rate_water.png");
  fd.append("cum_water", args.panels.water.cum, "cum_water.png");
  fd.append("map_image", args.panels.map, "map.png");
  if (args.panels.oil.probit) {
    fd.append("probit_oil", args.panels.oil.probit, "probit_oil.png");
  }
  if (args.panels.gas.probit) {
    fd.append("probit_gas", args.panels.gas.probit, "probit_gas.png");
  }
  if (args.panels.water.probit) {
    fd.append("probit_water", args.panels.water.probit, "probit_water.png");
  }
  const qs = args.compareWithId
    ? `?compareWith=${encodeURIComponent(args.compareWithId)}`
    : "";
  const r = await apiFetch(`/api/type-curves/${args.id}/export.pptx${qs}`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => "");
    throw new Error(`pptx export failed: ${r.status} ${detail}`);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = args.filename ?? "type_curve.pptx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
