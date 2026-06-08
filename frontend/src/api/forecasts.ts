// Forecast API typed client.
// Mirrors app/api/forecasts.py response shapes — keep in sync.

import { apiFetch } from "./auth";

export type Stream = "oil" | "gas" | "water";
// "rate_time_fallback" = the default rate_cum fit pinned Di at a bound,
// so the orchestrator re-ran with rate_time and adopted that result.
// See backend forecasting/fit.py::fit_with_fallback.
// "cohort_transfer" = short-history well whose Di / b were transferred
// from the median of the long-history cohort in the same batch.
// See backend forecasting/cohort.py + the /transfer-cohort-params endpoint.
export type FitMethod =
  | "rate_cum"
  | "rate_time"
  | "rate_time_fallback"
  | "cohort_transfer";
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
  // When set, the batch endpoint partitions the selection by oil
  // post-peak history and fits only the long-history group. Short-
  // history wells come back in BatchResponse.short_api10s for the
  // user to transfer once they've reviewed the long fits. Null =
  // legacy path (every well is fit unconstrained).
  short_history_cutoff_months: number | null;
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
  // Default: split the batch on a 6-month post-peak cutoff. Matches
  // the analyst convention this codebase was built around. The Map
  // tab's right panel exposes a control so the user can change it
  // before clicking Forecast.
  short_history_cutoff_months: 6,
};

export interface ForecastRow {
  id: string;
  api10: string;
  stream: Stream;
  model_type: ModelType;
  params: Record<string, number>;
  qi: number | null;
  di_initial: number | null;  // Arps nominal Di — what's in the rate formula
  di_effective: number | null; // first-year effective decline fraction (0–1)
  b: number | null;
  df_terminal: number | null;
  // Linear-ramp prefix: qo is the rate at first prod (t=0); the ramp
  // runs over peak_index_months before Arps takes over at peak. NULL
  // on rows from before the ramp columns existed and not yet re-fit.
  qo: number | null;
  peak_index_months: number | null;
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
  // Audit payload — populated for cohort_transfer rows with the
  // donor cohort identifiers and median. Null on standard fits.
  // Shape: {source, stream, donor_count, donor_api10s, cohort_Di,
  // cohort_b, cutoff_months}.
  diagnostics: Record<string, unknown> | null;
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
  // ISO date string from the wells table — drives the Review tab's
  // First Prod Date column. Kept alongside vintage_year because the
  // map tab still uses the integer year for histogram bucketing.
  well_first_prod_date: string | null;
  well_county: string | null;
  // Novi's 50-yr oil EUR — benchmark column the Review table renders
  // alongside the app's autoforecast EUR. Sourced from
  // curated.wells_enriched.eur_50yr_oil_bbl. Null when Novi hasn't
  // forecasted the well.
  well_novi_oil_eur: number | null;
  // Method-1 EUR triple, derived per request by the backend from
  // production aggregates. ``eur_displayed`` is the value to show in
  // user-facing surfaces: actual production to date + model's
  // projection from end-of-history to year 50. ``eur_remaining`` is
  // just the future portion. ``actual_cum`` is the raw past-to-date
  // volume (BBL for oil/water, MCF for gas).
  actual_cum: number | null;
  eur_remaining: number | null;
  eur_displayed: number | null;
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
  // Populated when the request set short_history_cutoff_months on the
  // config. The page uses these to render the "X pending transfer"
  // banner without needing a second round trip. Null on legacy
  // (no-cutoff) calls.
  long_api10s: string[] | null;
  short_api10s: string[] | null;
  no_peak_api10s: string[] | null;
}

export interface TransferStreamDonor {
  stream: Stream;
  donor_count: number;
  cohort_di: number;
  cohort_b: number;
}

export interface TransferResponse {
  written_api10s: string[];
  skipped_locked: Array<[string, Stream]>;
  skipped_no_peak: string[];
  long_api10s: string[];
  short_api10s: string[];
  donors: TransferStreamDonor[];
}

export interface TransferError {
  error: "insufficient_donor_cohort";
  donor_count: number;
  min_required: number;
  long_api10s: string[];
  short_api10s: string[];
  no_peak_api10s: string[];
}

export interface SyncJob {
  id: string;
  entity: string;
  scope_key: string;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  items_seen: number;
  items_upserted: number;
  items_failed: number;
  error: string | null;
}

export interface SyncStatus {
  recent_jobs: SyncJob[];
}

export interface StreamCurves {
  stream: Stream;
  months: string[];
  history_rate: Array<number | null>;
  history_cum: Array<number | null>;
  // Downtime-filtered variants for slide spaghetti — post-peak
  // months flagged by the same _flag_downtime the fitter used get
  // nulled in the rate, and the cum is re-integrated by linear
  // interpolation across those nulls. The per-well modal still uses
  // the unfiltered series; only the slide opts in.
  history_rate_filtered: Array<number | null>;
  history_cum_filtered: Array<number | null>;
  forecast_months: string[];
  forecast_rate: number[];
  forecast_cum: number[];
}

export interface WellCurvesResponse {
  api10: string;
  streams: StreamCurves[];
}

export interface PreviewResponse {
  t_years: number[];
  rate: number[];
  cum: number[];
  eur: number;
  // Method-1 metrics: populated by the backend when the preview
  // request carries an api10 + stream so it can look up the well's
  // actual cum and last-observed month. Null when the caller didn't
  // ask for Method-1 (e.g. TC-level previews).
  eur_displayed: number | null;
  eur_remaining: number | null;
  actual_cum: number | null;
}

export async function batchForecast(
  api10s: string[],
  config?: Partial<ForecastConfig>,
): Promise<BatchResponse> {
  const r = await apiFetch("/api/forecasts/batch", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ api10s, config }),
  });
  if (!r.ok) throw new Error(`batch forecast failed: ${r.status}`);
  return (await r.json()) as BatchResponse;
}

// Re-fits short-history wells by transferring Di / b from the long-
// history cohort's median. Throws a TransferErrorThrown on 422 so the
// caller can pattern-match on `.payload` for the structured error
// shape (donor count + the split that drove the failure).
export class TransferErrorThrown extends Error {
  constructor(public payload: TransferError) {
    super(`insufficient_donor_cohort: ${payload.donor_count}/${payload.min_required}`);
  }
}

export async function transferCohortParams(args: {
  api10s: string[];
  short_history_cutoff_months: number;
  min_donor_count?: number;
  config?: Partial<ForecastConfig>;
}): Promise<TransferResponse> {
  const r = await apiFetch("/api/forecasts/transfer-cohort-params", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(args),
  });
  if (r.status === 422) {
    const body = (await r.json()) as { detail: TransferError };
    throw new TransferErrorThrown(body.detail);
  }
  if (!r.ok) throw new Error(`transfer failed: ${r.status}`);
  return (await r.json()) as TransferResponse;
}

export async function listForecasts(api10s: string[]): Promise<ForecastRow[]> {
  if (api10s.length === 0) return [];
  const q = new URLSearchParams();
  api10s.forEach((a) => q.append("api10", a));
  const r = await apiFetch(`/api/forecasts?${q}`);
  if (!r.ok) throw new Error(`list forecasts failed: ${r.status}`);
  return (await r.json()) as ForecastRow[];
}

export async function fetchSyncStatus(): Promise<SyncStatus> {
  const r = await apiFetch("/api/sync/status");
  if (!r.ok) throw new Error(`sync status failed: ${r.status}`);
  return (await r.json()) as SyncStatus;
}

// Direct lookup of a single job by id. The polling path uses this
// rather than scanning recent_jobs — the /sync/status endpoint caps
// at 20 rows and an active session can push an in-flight job off
// that window, leaving the poller spinning forever.
export async function fetchSyncJob(id: string): Promise<SyncJob | null> {
  const r = await apiFetch(`/api/sync/jobs/${id}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`sync job lookup failed: ${r.status}`);
  return (await r.json()) as SyncJob;
}

export async function fetchWellCurves(
  api10: string,
  horizonYears = 50,
  // Optional TC context: when provided, any saved per-stream
  // override on that type curve takes precedence over the global
  // forecast row in the returned forecast portion. The modal sends
  // this when open in TC context so "Save TC override" reflects in
  // the chart on refetch.
  tcId?: string | null,
): Promise<WellCurvesResponse> {
  const params = new URLSearchParams({ horizon_years: String(horizonYears) });
  if (tcId) params.set("tc_id", tcId);
  const r = await apiFetch(`/api/forecasts/${api10}/curves?${params.toString()}`);
  if (!r.ok) throw new Error(`well curves failed: ${r.status}`);
  return (await r.json()) as WellCurvesResponse;
}

export async function previewForecast(args: {
  model_type: ModelType;
  params: Record<string, number>;
  economic_limit?: number;
  horizon_years?: number;
  n_points?: number;
  // Pass both to opt into the Method-1 displayed-EUR computation in
  // the response. Omit for TC-level previews where there's no single
  // well's actual cum to layer in.
  api10?: string;
  stream?: Stream;
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
