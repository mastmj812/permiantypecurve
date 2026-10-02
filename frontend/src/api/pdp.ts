// PDP forecasting from seller data-room daily production. Mirrors
// backend app/api/pdp.py. Method of record: app/forecasting/daily.py —
// producing-day fit x per-well uptime, time origin at each stream's own
// peak, unpeaked streams decline now on the same-bench cohort Di.

import { apiFetch } from "./auth";

export type PdpStream = "oil" | "gas" | "water";
export type PdpMethod = "daily_fit" | "transfer_now" | "manual" | "shut_in";

export const STREAM_COLOR: Record<PdpStream, string> = {
  oil: "#16a34a",
  gas: "#dc2626",
  water: "#2563eb",
};

export interface VdrSource {
  vdr_id: string;
  deal: string;
  vendor_format: string;
  loaded_at: string;
  data_through: string | null;
  n_wells: number;
}

export interface PdpConfig {
  vdr_id: string;
  api10s?: string[] | null;
  // Omitted on a data-room save: the backend merges, so stored overrides
  // and export settings survive.
  uptime_overrides?: Record<string, number>;
  // Seller 2PDNP wells convey (zero forecast unless a restart is set).
  include_pdnp?: boolean;
  export?: PdpExportConfig;
}

export interface PdpBreak {
  kind: "shut_in" | "choke_change";
  start: string;
  end?: string;
  days?: number;
  choke_from?: number;
  choke_to?: number;
  early_life?: boolean;
  // choke changes only: oil rate stepped >= 15% (or step unmeasurable)
  material?: boolean;
  oil_rate_before: number | null;
  oil_rate_after: number | null;
  rate_ratio: number | null;
}

export interface PdpDonors {
  di_median: number;
  di_p25: number;
  di_p75: number;
  n: number;
  n_candidates: number;
  n_autofit_now: number;
  radius_mi: number;
  benches: string[];
  by_bench: Record<string, number>;
}

export interface PdpRow {
  api10: string;
  well_name: string | null;
  formation_blueox: string | null;
  stream: PdpStream;
  method: PdpMethod;
  qi: number;
  Di: number;
  b: number;
  Df: number;
  anchor_date: string;
  fit_start_date: string | null;
  data_through: string;
  uptime_factor: number;
  uptime_basis: {
    factor: number;
    override?: number;
    event_days: number;
    routine_down_days: number;
  };
  fit_r2_log: number | null;
  n_points: number;
  tail_ratio: number | null;
  cum_to_date: number;
  remaining: number;
  eur: number;
  review_flags: string[];
  breaks: PdpBreak[];
  diagnostics: {
    anchor_effective_decline?: number;
    forward_effective_decline?: number;
    at_bound?: string | null;
    donors?: PdpDonors;
    previous?: {
      method: string;
      params: Record<string, number>;
      anchor_date: string;
    };
  };
  manual_override: boolean;
  locked: boolean;
}

export interface PdpSeries {
  api10: string;
  stream: PdpStream;
  method: PdpMethod;
  anchor_date: string;
  fit_start_date: string | null;
  data_through: string;
  uptime_factor: number;
  actual: Array<{ d: string; q: number | null; down: boolean }>;
  model: Array<{ d: string; q: number }>;
  events: Array<{ start: string; end: string }>;
  breaks: PdpBreak[];
}

export interface RunOutcome {
  counts: Record<string, number>;
  wells: Record<string, Record<PdpStream, string>>;
}

async function detail(r: Response): Promise<string> {
  try {
    const body = (await r.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : `${r.status}`;
  } catch {
    return `${r.status}`;
  }
}

async function jsonOrThrow<T>(r: Response, what: string): Promise<T> {
  if (!r.ok) throw new Error(`${what} failed: ${await detail(r)}`);
  return (await r.json()) as T;
}

const JSON_HEADERS = { "content-type": "application/json" };

export async function listVdrSources(): Promise<VdrSource[]> {
  return jsonOrThrow(await apiFetch("/api/vdr/sources"), "list data rooms");
}

export async function getPdpConfig(dealId: string): Promise<PdpConfig | null> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/config`),
    "get PDP config",
  );
}

export async function putPdpConfig(
  dealId: string,
  cfg: PdpConfig,
): Promise<PdpConfig> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/config`, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify(cfg),
    }),
    "save PDP config",
  );
}

export async function syncPdp(dealId: string): Promise<{
  wells: number;
  rows: number;
  data_through: string | null;
  missing_api10s: string[];
}> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/sync`, { method: "POST" }),
    "sync daily production",
  );
}

export async function runPdpForecast(
  dealId: string,
  api10s?: string[],
): Promise<RunOutcome> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/forecast`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ api10s: api10s ?? null }),
    }),
    "run PDP forecast",
  );
}

export async function listPdpForecasts(dealId: string): Promise<PdpRow[]> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/forecasts`),
    "list PDP forecasts",
  );
}

export async function fetchPdpSeries(
  dealId: string,
  api10: string,
  stream: PdpStream,
  yearsAhead = 5,
): Promise<PdpSeries> {
  return jsonOrThrow(
    await apiFetch(
      `/api/deals/${dealId}/pdp/forecasts/${api10}/${stream}/series?years_ahead=${yearsAhead}`,
    ),
    "load chart series",
  );
}

export interface PdpPatch {
  fit_start_date?: string;
  clear_fit_start?: boolean;
  params?: { qi: number; Di: number; b: number; anchor_date: string };
  locked?: boolean;
}

export async function patchPdpForecast(
  dealId: string,
  api10: string,
  stream: PdpStream,
  body: PdpPatch,
): Promise<PdpRow> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/forecasts/${api10}/${stream}`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(body),
    }),
    `update ${api10}/${stream}`,
  );
}

export async function putWellUptime(
  dealId: string,
  api10: string,
  uptime: number | null,
): Promise<RunOutcome> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/wells/${api10}/uptime`, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({ uptime }),
    }),
    `set uptime for ${api10}`,
  );
}

// Arps nominal -> 1-yr effective (fraction), same math as the backend's
// metrics.effective_decline_first_year: hyperbolic 1-(1+b*Di)^(-1/b),
// exponential limit at b -> 0.
export function effectiveFromNominal(Di: number, b: number): number {
  if (b < 1e-6) return 1 - Math.exp(-Di);
  return 1 - Math.pow(1 + b * Di, -1 / b);
}

// ---------------- Blue Ox PDP workbook (contract §2) ----------------

export type PdpGrouping = "well" | "lease" | "custom";

export interface PdpExportConfig {
  effective_date: string;
  grouping: PdpGrouping;
  groups?: Record<string, string[]>;
  curve_months?: number | null;
  supersedes?: string | null;
}

export interface PdpExportPreview {
  filename: string;
  first_row_month: string;
  curve_months: number;
  production_history_through: string;
  groups: Array<{
    name: string;
    well_count: number;
    eur_oil: number;
    eur_gas: number;
    eur_water: number;
  }>;
  findings: Array<{ status: "warn" | "fail"; check: string; detail: string }>;
  contract_errors: string | null;
}

export async function putPdpExportConfig(
  dealId: string,
  cfg: PdpExportConfig,
): Promise<PdpExportConfig> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/export-config`, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify(cfg),
    }),
    "save export settings",
  );
}

export async function previewPdpExport(
  dealId: string,
): Promise<PdpExportPreview> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/export/preview`),
    "preview PDP workbook",
  );
}

// Blob download — <a download> can't attach the bearer token (same trick
// as downloadDealExport).
export async function downloadPdpExport(
  dealId: string,
  filename: string,
): Promise<void> {
  const r = await apiFetch(`/api/deals/${dealId}/pdp/export.xlsx`);
  if (!r.ok) throw new Error(`PDP workbook failed: ${await detail(r)}`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Well-level sign-off: (un)lock every stream of the listed wells.
export async function lockWells(
  dealId: string,
  api10s: string[],
  locked = true,
): Promise<{ wells: number; streams: number; changed: number }> {
  return jsonOrThrow(
    await apiFetch(`/api/deals/${dealId}/pdp/lock`, {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ api10s, locked }),
    }),
    locked ? "lock wells" : "unlock wells",
  );
}

// Review-flag and method labels shared by the PDP tab components.
export const PDP_FLAG_TEXT: Record<string, string> = {
  unpeaked_transfer:
    "Still inclining — declines now on the same-bench cohort Di",
  at_bound: "A fit parameter sits on its bound",
  tail_mismatch: "Model vs last-90-day actual outside ±15%",
  recent_break: "Shut-in or material choke change in the last 18 months",
  donor_water_allocated: "Donor water is Novi TX allocation (0.970 × gas)",
  shut_in:
    "No producing day in the last 365 d — forecast zero; set manual params with a future anchor for a restart",
};

export const PDP_METHOD_LABEL: Record<string, string> = {
  daily_fit: "fit",
  transfer_now: "cohort",
  manual: "manual",
  shut_in: "shut-in",
};

export const PDP_FLAG_SHORT: Record<string, string> = {
  unpeaked_transfer: "unpeaked",
  tail_mismatch: "tail",
  recent_break: "break",
  donor_water_allocated: "donor H₂O",
  shut_in: "shut-in",
};
