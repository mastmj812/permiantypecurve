// Forecast API typed client.
// Mirrors app/api/forecasts.py response shapes — keep in sync.

import { apiFetch } from "./auth";

export type Stream = "oil" | "gas" | "water";
// "rate_time_fallback" = the default rate_cum fit pinned Di at a bound,
// so the orchestrator re-ran with rate_time and adopted that result.
// See backend forecasting/fit.py::fit_with_fallback.
export type FitMethod = "rate_cum" | "rate_time" | "rate_time_fallback";
export type ModelType =
  | "arps_exponential"
  | "arps_hyperbolic"
  | "arps_harmonic"
  | "modified_hyperbolic"
  | "duong";

export interface ForecastConfig {
  model_type: ModelType;
  fit_method: FitMethod;
  df_terminal_per_year: number;
  horizon_years: number;
  economic_limit_bopd: number;
  economic_limit_mcfd: number;
  economic_limit_bwpd: number;
  min_post_peak_months: number;
}

export const DEFAULT_FORECAST_CONFIG: ForecastConfig = {
  model_type: "modified_hyperbolic",
  fit_method: "rate_cum",
  df_terminal_per_year: 0.08,
  horizon_years: 50,
  // Economic-limit defaults are 0 — this tool is a TECHNICAL type-curve /
  // decline generator and EUR is the raw 50-yr integral. Economics
  // happens downstream on the exported workbook.
  economic_limit_bopd: 0,
  economic_limit_mcfd: 0,
  economic_limit_bwpd: 0,
  min_post_peak_months: 6,
};

export interface ForecastRow {
  id: string;
  api14: string;
  stream: Stream;
  model_type: ModelType;
  params: Record<string, number>;
  qi: number | null;
  di_initial: number | null;  // Arps nominal Di — what's in the rate formula
  di_effective: number | null; // first-year effective decline fraction (0–1)
  b: number | null;
  df_terminal: number | null;
  eur: number | null;
  peak_month_date: string | null;
  peak_rate: number | null;
  fit_method: FitMethod;
  fit_r2: number | null;
  fit_rmse: number | null;
  fit_at_bound: boolean;     // qi/Di/b pinned at a bound — engineer should review
  bound_note: string | null; // human-readable specifics when fit_at_bound is true
  // Fraction of post-peak months excluded as downtime (0–1). null on
  // forecasts persisted before the column existed.
  downtime_ratio: number | null;
  manual_override: boolean;
  locked: boolean;
  updated_at: string;
  // Joined well attributes — populated by /list (the review/forecast
  // grids use these for sort + display); null when fetched by id.
  well_name: string | null;
  well_operator: string | null;
  well_formation: string | null;
  well_lateral_ft: number | null;
  well_vintage_year: number | null;
  well_county: string | null;
}

// Client-side conversion mirroring app/forecasting/metrics.py.
// Used for live preview while the user edits qi/Di/b in the detail modal —
// the API also returns di_effective for the un-edited fit.
export function effectiveDecline(Di: number, b: number | null | undefined): number {
  if (!Number.isFinite(Di) || Di <= 0) return 0;
  if (b == null || Math.abs(b) < 1e-6) return 1 - Math.exp(-Di);
  if (Math.abs(b - 1) < 1e-4) return Di / (1 + Di);
  return 1 - 1 / Math.pow(1 + b * Di, 1 / b);
}

// Inverse of effectiveDecline — solve for nominal Di given first-year
// effective decline (0–1) and b. Lets the UI present Di in industry
// convention (% effective) while keeping the Arps formula in nominal
// units. Cap eff at 0.999 so the hyperbolic branch doesn't divide by
// (1 - eff)^b at the singularity.
export function nominalDecline(diEffective: number, b: number | null | undefined): number {
  if (!Number.isFinite(diEffective) || diEffective <= 0) return 0;
  const eff = Math.min(diEffective, 0.999);
  if (b == null || Math.abs(b) < 1e-6) return -Math.log(1 - eff);
  if (Math.abs(b - 1) < 1e-4) return eff / (1 - eff);
  return (Math.pow(1 - eff, -b) - 1) / b;
}

export interface BatchResponse {
  accepted: boolean;
  job_id: string;
  well_count: number;
}

export interface SyncStatus {
  recent_jobs: Array<{
    id: string;
    entity: string;
    scope_key: string;
    status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
    items_seen: number;
    items_upserted: number;
    items_failed: number;
    error: string | null;
  }>;
}

export interface StreamCurves {
  stream: Stream;
  months: string[];
  history_rate: Array<number | null>;
  history_prodday_rate: Array<number | null>;
  history_cum: Array<number | null>;
  forecast_months: string[];
  forecast_rate: number[];
  forecast_cum: number[];
}

export interface WellCurvesResponse {
  api14: string;
  streams: StreamCurves[];
}

export interface PreviewResponse {
  t_years: number[];
  rate: number[];
  cum: number[];
  eur: number;
}

export async function batchForecast(
  api14s: string[],
  config?: Partial<ForecastConfig>,
): Promise<BatchResponse> {
  const r = await apiFetch("/api/forecasts/batch", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ api14s, config }),
  });
  if (!r.ok) throw new Error(`batch forecast failed: ${r.status}`);
  return (await r.json()) as BatchResponse;
}

export async function listForecasts(api14s: string[]): Promise<ForecastRow[]> {
  if (api14s.length === 0) return [];
  const q = new URLSearchParams();
  api14s.forEach((a) => q.append("api14", a));
  const r = await apiFetch(`/api/forecasts?${q}`);
  if (!r.ok) throw new Error(`list forecasts failed: ${r.status}`);
  return (await r.json()) as ForecastRow[];
}

export async function fetchSyncStatus(): Promise<SyncStatus> {
  const r = await apiFetch("/api/sync/status");
  if (!r.ok) throw new Error(`sync status failed: ${r.status}`);
  return (await r.json()) as SyncStatus;
}

export async function fetchWellCurves(
  api14: string,
  horizonYears = 50,
): Promise<WellCurvesResponse> {
  const r = await apiFetch(`/api/forecasts/${api14}/curves?horizon_years=${horizonYears}`);
  if (!r.ok) throw new Error(`well curves failed: ${r.status}`);
  return (await r.json()) as WellCurvesResponse;
}

export async function previewForecast(args: {
  model_type: ModelType;
  params: Record<string, number>;
  economic_limit?: number;
  horizon_years?: number;
  n_points?: number;
}): Promise<PreviewResponse> {
  const r = await apiFetch("/api/forecasts/preview", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!r.ok) throw new Error(`preview failed: ${r.status}`);
  return (await r.json()) as PreviewResponse;
}

export async function patchForecast(
  id: string,
  body: {
    params?: Record<string, number>;
    locked?: boolean;
    manual_override?: boolean;
    economic_limit?: number;
  },
): Promise<ForecastRow> {
  const r = await apiFetch(`/api/forecasts/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`patch forecast failed: ${r.status}`);
  return (await r.json()) as ForecastRow;
}
