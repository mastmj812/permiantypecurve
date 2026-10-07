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
// PUD = proven undeveloped (scheduled/valued); UPSIDE = non-proven,
// carried unscheduled. Same vocabulary as the per-well handoff
// category. (Legacy "RES" is coerced to UPSIDE server-side.)
export type BlueOxCategory = "PUD" | "UPSIDE";

// One narvi scenario reference (a scenario ≈ one DSU).
export interface BlueOxScenarioRef {
  deal_id: string;
  scenario_id: string;
}

export interface BlueOxZoneSpec {
  type_curve_id: string;
  // adopted verbatim by Blue Ox as the zone identifier (<= 26 chars,
  // stable across re-drops); null defaults to the curve name server-side
  zone_name: string | null;
  reserve_category: BlueOxCategory;
  // narvi formation_blueox bench codes whose wells belong to this zone.
  // Benches must be disjoint across zones WITHIN OVERLAPPING SCENARIO
  // SCOPE — two zones may claim one bench when their scopes are disjoint.
  benches: string[];
  // Scenario scope: null/absent/[] = ALL selected scenarios (legacy
  // behavior). Otherwise the subset of narvi_selections this zone's
  // benches capture — the geographic grain for same-bench west/east
  // curve splits on wide deals.
  scenario_scope?: BlueOxScenarioRef[] | null;
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

export interface NarviBenchCount {
  formation: string | null; // formation_blueox bench code
  category: string; // PDP / PUD / UPSIDE
  n: number;
}

export interface NarviScenario {
  deal_id: string;
  scenario_id: string;
  name: string | null;
  well_type: string;
  total_wells: number | null;
  total_completed_ft: number | null;
  updated_at: string;
  // per-bench well counts by handoff class — the pre-export surface for
  // confirming zone categories and bench coverage
  breakdown: NarviBenchCount[];
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

// ---------------- deal dossier (working discussion deck) ----------------

export interface DossierManifest {
  scenarios: Array<{ title: string; subtitle: string }>;
  curves: Array<{ type_curve_id: string }>;
  // TC-vs-Novi comparison slides (one per zone with sticks); files
  // named n{i}_figure. Optional — older callers omit it.
  comparisons?: Array<{ title: string; subtitle: string }>;
  // Curve-assignment overview (one full-width map, file overview_map)
  // and per-zone curve support slides (files z{i}_map + z{i}_zoom).
  overview?: { title: string; subtitle: string } | null;
  supports?: Array<{ title: string; subtitle: string }>;
}

// ---------------- TC-vs-Novi comparison (dossier figure data) ----------------

export interface NoviComparisonZone {
  zone_name: string;
  type_curve_id: string;
  n_sticks: number;
  n_self: number;
  n_neighborhood: number;
  n_pud: number;
  n_res: number;
  n_wells_no_set: number;
  radius_m: number;
  // per-basin since 2026-07-30 (delaware 0.25 / midland 0.40)
  lateral_tol: number;
  low_n: boolean;
  stale_vintage: boolean;
  tc_risked: boolean;
  intel_vintage: string | null;
  step_days: number;
  // median Novi ML per-day rates per 1,000 ft, IP-aligned 30-day grid
  oil_rate: number[];
  gas_rate: number[];
  water_rate: number[];
  // previous-vintage overlay: the persisted drop-time stick set plotted
  // against its OWN (superseded) vintage; null/empty when every
  // contributing set was saved under the current vintage
  prev_intel_vintage: string | null;
  prev_n_sticks: number;
  prev_oil_rate: number[];
  prev_gas_rate: number[];
  prev_water_rate: number[];
}

export async function fetchNoviComparison(
  dealId: string,
): Promise<NoviComparisonZone[]> {
  const r = await apiFetch(`/api/deals/${dealId}/novi-comparison`);
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`novi comparison fetch failed: ${detail}`);
  }
  const body = (await r.json()) as { zones: NoviComparisonZone[] };
  return body.zones;
}

// ---------------- dossier zone summary (deck's opening slide) ----------------

// One planned stick routed to a zone (PDP context excluded); legs are
// [heel_lon, heel_lat, toe_lon, toe_lat] per producing leg.
export interface DossierStick {
  well_name: string;
  formation: string | null;
  category: string; // PUD | UPSIDE (handoff category)
  scenario_ref: string; // "<deal_id>/<scenario_id>"
  completed_lateral_ft: number | null;
  target_tvd_ft: number | null;
  legs_lonlat: number[][];
}

// One well that builds the zone's curve: wellstick + anduin's resolved
// per-well oil EUR/ft (override -> global, raw 50-yr, unrisked — the
// /well-stats number).
export interface DossierCohortWell {
  api10: string;
  name: string | null;
  lateral_ft: number | null;
  oil_eur_per_ft: number | null;
  coords: number[][];
}

export interface DossierStreamSummary {
  eur_per_1000ft: number | null; // risked P50, raw 50-yr
  qi_per_1000ft: number | null;
  di_nominal: number | null; // per year
  di_effective: number | null; // 1-yr secant, 0-1
  b: number | null;
  risk_mult: number;
  novi_eur_per_1000ft: number | null;
  tc_vs_novi: number | null; // TC / Novi - 1
  gap_flag: boolean;
}

export interface DossierZone {
  zone_name: string;
  type_curve_id: string;
  curve_name: string;
  reserve_category: string;
  benches: string[];
  n_sticks: number;
  n_pud: number;
  n_upside: number;
  n_scenarios: number;
  planned_lateral_ft_median: number | null;
  streams: Record<"oil" | "gas", DossierStreamSummary>;
  qc: {
    n_wells: number;
    n_overridden: number;
    at_bound: Record<string, number>;
    missing: Record<string, number>;
  };
  novi_n_sticks: number;
  novi_low_n: boolean;
  novi_stale: boolean;
  novi_error: string | null;
  flags: string[];
  // pre-formatted row, same strings as the deck's summary slide
  cells: string[];
  sticks: DossierStick[];
  cohort: DossierCohortWell[];
}

// One curve's well table (deck: before its Oil/Gas/Water slides); cells
// pre-formatted server-side, nearest the planned sticks first.
export interface DossierCurveTable {
  type_curve_id: string;
  curve_name: string;
  zones: string[];
  rows: string[][];
}

export interface DossierZonesResponse {
  headers: string[];
  note: string;
  zones: DossierZone[];
  cohort_headers: string[];
  cohort_note: string;
  curve_tables: DossierCurveTable[];
}

export async function fetchDossierZones(dealId: string): Promise<DossierZonesResponse> {
  const r = await apiFetch(`/api/deals/${dealId}/dossier-zones`);
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`dossier zone summary fetch failed: ${detail}`);
  }
  return (await r.json()) as DossierZonesResponse;
}

// POSTs the manifest + client-captured panel PNGs; file names must
// match the backend's convention (s{i}_map / s{i}_gunbarrel and
// c{i}_rate_{stream} / c{i}_cum_{stream} / c{i}_map).
export async function exportDealDossierPptx(
  dealId: string,
  manifest: DossierManifest,
  files: Record<string, Blob>,
): Promise<string> {
  const fd = new FormData();
  fd.append("manifest", JSON.stringify(manifest));
  for (const [name, blob] of Object.entries(files)) {
    fd.append(name, blob, `${name}.png`);
  }
  const r = await apiFetch(`/api/deals/${dealId}/dossier.pptx`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`dossier export failed: ${detail}`);
  }
  const cd = r.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(cd)?.[1] ?? "deal_dossier.pptx";
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
