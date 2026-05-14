// Forecast API typed client.
// Mirrors app/api/forecasts.py response shapes — keep in sync.

export type Stream = "oil" | "gas" | "water";
export type FitMethod = "rate_cum" | "rate_time";
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
  economic_limit_bopd: 5,
  economic_limit_mcfd: 30,
  economic_limit_bwpd: 50,
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
  manual_override: boolean;
  locked: boolean;
  updated_at: string;
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
  const r = await fetch("/api/forecasts/batch", {
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
  const r = await fetch(`/api/forecasts?${q}`);
  if (!r.ok) throw new Error(`list forecasts failed: ${r.status}`);
  return (await r.json()) as ForecastRow[];
}

export async function fetchSyncStatus(): Promise<SyncStatus> {
  const r = await fetch("/api/sync/status");
  if (!r.ok) throw new Error(`sync status failed: ${r.status}`);
  return (await r.json()) as SyncStatus;
}

export async function fetchWellCurves(
  api14: string,
  horizonYears = 50,
): Promise<WellCurvesResponse> {
  const r = await fetch(`/api/forecasts/${api14}/curves?horizon_years=${horizonYears}`);
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
  const r = await fetch("/api/forecasts/preview", {
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
  const r = await fetch(`/api/forecasts/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`patch forecast failed: ${r.status}`);
  return (await r.json()) as ForecastRow;
}
