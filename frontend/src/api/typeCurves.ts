// Type curve API client. Mirrors app/api/type_curves.py.

import { apiFetch } from "./auth";

export type NormalizationBasis = "per_lateral_ft" | "per_proppant_lb" | "per_well";
export type AlignmentMethod = "peak_month" | "first_prod_month";

export interface FittedTypeCurve {
  model_type: string;
  qi: number;
  Di: number;          // nominal per-year
  b: number;
  Df: number;
  eur_per_unit: number; // BBL or MCF per 1,000 lateral ft (total: ramp + Arps)
  r2?: number;          // present on auto-fit, absent on manual override
  rmse?: number;        // ditto
  smoothed_rate: number[];
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
}

export interface TypeCurveRow extends TypeCurveSummary {
  filter_spec: Record<string, unknown>;
  included_api14s: string[];
  series: AggregatePayload;
}

export async function computeTypeCurve(args: {
  api14s: string[];
  normalization_basis?: NormalizationBasis;
  alignment_method?: AlignmentMethod;
  n_months?: number | null;
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
  included_api14s: string[];
  normalization_basis?: NormalizationBasis;
  alignment_method?: AlignmentMethod;
  n_months?: number | null;
  version_of?: string | null;
  // Optional per-stream override of the fitted curve. Server re-evaluates
  // these and bakes them into the saved series.
  fit_overrides?: Record<string, FitOverride> | null;
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
      included_api14s: args.included_api14s,
      normalization_basis: args.normalization_basis ?? "per_lateral_ft",
      alignment_method: args.alignment_method ?? "first_prod_month",
      n_months: args.n_months ?? null,
      fit_overrides: args.fit_overrides ?? null,
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
  api14: string;
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
