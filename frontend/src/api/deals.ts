// Deal API client. Mirrors app/api/deals.py.
//
// A deal groups type curves so the engineer can export the whole package
// as one Excel workbook (per curve: a metadata sheet + a fitted-forecast
// sheet for oil/gas/water, out to 50 years).

import { apiFetch } from "./auth";
import type { TypeCurveSummary } from "./typeCurves";

export interface DealSummary {
  id: string;
  name: string;
  notes: string | null;
  created_at: string;
  n_curves: number;
}

export interface DealRow {
  id: string;
  name: string;
  notes: string | null;
  created_at: string;
  curves: TypeCurveSummary[];
}

export async function listDeals(): Promise<DealSummary[]> {
  const r = await apiFetch("/api/deals");
  if (!r.ok) throw new Error(`list deals failed: ${r.status}`);
  return (await r.json()) as DealSummary[];
}

export async function getDeal(id: string): Promise<DealRow> {
  const r = await apiFetch(`/api/deals/${id}`);
  if (!r.ok) throw new Error(`fetch deal failed: ${r.status}`);
  return (await r.json()) as DealRow;
}

export async function createDeal(args: {
  name: string;
  notes?: string | null;
}): Promise<DealRow> {
  const r = await apiFetch("/api/deals", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name: args.name, notes: args.notes ?? null }),
  });
  if (!r.ok) {
    // 409 on duplicate name — surface the server's detail so the user
    // sees "deal name already exists" rather than a bare status code.
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`create deal failed: ${detail}`);
  }
  return (await r.json()) as DealRow;
}

export async function patchDeal(
  id: string,
  body: { name?: string; notes?: string | null },
): Promise<DealRow> {
  const r = await apiFetch(`/api/deals/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`patch deal failed: ${detail}`);
  }
  return (await r.json()) as DealRow;
}

export async function deleteDeal(id: string): Promise<void> {
  const r = await apiFetch(`/api/deals/${id}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) {
    throw new Error(`delete deal failed: ${r.status}`);
  }
}

// xlsx export — same blob-download trick as downloadTypeCurveExport,
// because <a download> can't attach the bearer token.
export async function downloadDealExport(
  id: string,
  filename = "deal.xlsx",
): Promise<void> {
  const r = await apiFetch(`/api/deals/${id}/export.xlsx`);
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`export deal failed: ${detail}`);
  }
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

async function safeDetail(r: Response): Promise<string | null> {
  try {
    const body = (await r.json()) as { detail?: string };
    return body.detail ?? null;
  } catch {
    return null;
  }
}

// ---------------- Blue Ox curve-drop config (one deal = one workbook) --------

export type BlueOxLevel = "P10" | "P25" | "P75" | "P90";
export type BlueOxCategory = "PUD" | "RES";

export interface BlueOxZoneSpec {
  type_curve_id: string;
  // adopted verbatim by Blue Ox as the zone identifier (<= 26 chars,
  // stable across re-drops); null defaults to the curve name server-side
  zone_name: string | null;
  reserve_category: BlueOxCategory;
  // narvi formation_blueox bench codes whose wells belong to this zone
  benches: string[];
}

export interface BlueOxNarviSelection {
  deal_id: string;
  scenario_id: string;
  pinned_updated_at?: string | null; // stamped server-side on save
}

export interface BlueOxConfig {
  codename: string;
  curve_months: number;
  levels: BlueOxLevel[];
  prepared_by: string;
  zones: BlueOxZoneSpec[];
  narvi_selections: BlueOxNarviSelection[];
  exclude_benches: string[];
}

export interface BlueOxScenarioStatus {
  deal_id: string;
  scenario_id: string;
  pinned_updated_at: string | null;
  current_updated_at: string | null;
  stale: boolean;
}

export interface BlueOxConfigResponse {
  config: BlueOxConfig | null;
  narvi_status: BlueOxScenarioStatus[];
}

export interface NarviScenario {
  deal_id: string;
  scenario_id: string;
  name: string | null;
  well_type: string;
  total_wells: number | null;
  total_completed_ft: number | null;
  updated_at: string;
}

export async function getBlueOxConfig(dealId: string): Promise<BlueOxConfigResponse> {
  const r = await apiFetch(`/api/deals/${dealId}/blueox-config`);
  if (!r.ok) throw new Error(`fetch blue ox config failed: ${r.status}`);
  return (await r.json()) as BlueOxConfigResponse;
}

export async function putBlueOxConfig(
  dealId: string,
  cfg: BlueOxConfig,
): Promise<BlueOxConfigResponse> {
  const r = await apiFetch(`/api/deals/${dealId}/blueox-config`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(cfg),
  });
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`save blue ox config failed: ${detail}`);
  }
  return (await r.json()) as BlueOxConfigResponse;
}

export async function listNarviScenarios(): Promise<NarviScenario[]> {
  const r = await apiFetch("/api/narvi/scenarios");
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`narvi scenario listing failed: ${detail}`);
  }
  return (await r.json()) as NarviScenario[];
}

// Thrown when the export refuses because a pinned narvi scenario was
// re-saved since the config was pinned; the caller may confirm and
// retry with allowStale.
export class BlueOxStaleError extends Error {}

export async function downloadBlueOxExport(
  dealId: string,
  opts: { allowStale?: boolean } = {},
): Promise<string> {
  const qs = opts.allowStale ? "?allow_stale=true" : "";
  const r = await apiFetch(`/api/deals/${dealId}/blueox-export.xlsx${qs}`, {
    method: "POST",
  });
  if (r.status === 409) {
    throw new BlueOxStaleError((await safeDetail(r)) ?? "narvi scenarios changed");
  }
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`blue ox export failed: ${detail}`);
  }
  // filename comes from Content-Disposition (<codename>_curves_<date>.xlsx)
  const cd = r.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(cd)?.[1] ?? "blueox_curves.xlsx";
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return filename;
}
