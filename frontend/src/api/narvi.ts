// narvi scenario geometry client. Mirrors the narvi passthroughs in
// app/api/deals.py (narvi_router) — read-only; narvi is the single
// writer of narvi.* in the warehouse.

import { apiFetch } from "./auth";

export interface NarviWellGeo {
  well_name: string;
  formation: string | null; // formation_blueox bench code
  category: string; // resolved handoff class: PDP / PUD / UPSIDE
  provenance: string | null; // narvi source: generated / pud / res / pdp
  well_type: string; // single / uturn
  n_legs: number;
  completed_lateral_ft: number | null;
  target_tvd_ft: number | null;
  legs_geojson: string | null; // MultiLineString, WGS84
  turn_geojson: string | null; // LineString (U-turn arc), WGS84
  // Perpendicular cross-section offsets (ft), one per leg — narvi's
  // gunbarrel_x_ft.
  gunbarrel_xs: number[];
}

export interface NarviScenarioDetail {
  deal_id: string;
  scenario_id: string;
  name: string | null;
  well_type: string;
  // Gunbarrel frame azimuth of record (axial, [0, 180)) — every
  // gunbarrel_x_ft projects onto the axis 90° clockwise of it. Null on
  // legacy saves.
  azimuth_deg: number | null;
  aoi_geojson: string | null; // parcel Polygon/MultiPolygon, WGS84
  wells: NarviWellGeo[];
}

export async function fetchNarviScenarioDetail(
  dealId: string,
  scenarioId: string,
): Promise<NarviScenarioDetail> {
  const qs = new URLSearchParams({ deal_id: dealId, scenario_id: scenarioId });
  const r = await apiFetch(`/api/narvi/scenario-detail?${qs}`);
  if (!r.ok) {
    throw new Error(`narvi scenario detail failed: ${r.status}`);
  }
  return (await r.json()) as NarviScenarioDetail;
}

export interface NarviDealStickWell {
  deal_id: string;
  scenario_id: string;
  scenario_name: string | null;
  well_name: string;
  formation: string | null; // RAW formation_blueox bench code, may carry _b
  category: string; // PUD / UPSIDE (PDP excluded server-side)
  well_type: string; // single / uturn
  legs_geojson: string | null; // MultiLineString, WGS84
  turn_geojson: string | null; // LineString (U-turn arc), WGS84
}

export interface NarviDealSticks {
  deal_ids: string[]; // requested ids that matched a scenario
  missing_deal_ids: string[]; // requested ids that matched nothing
  wells: NarviDealStickWell[];
}

// All planned (non-PDP) sticks across the selected narvi deals — narvi
// deal_ids are per-DSU (e.g. vault_dsu_*), so an engineer's "deal" is a
// set of them and the overlay fetches the whole set in one call.
export async function fetchNarviDealSticks(dealIds: string[]): Promise<NarviDealSticks> {
  const qs = new URLSearchParams();
  for (const id of dealIds) qs.append("deal_id", id);
  const r = await apiFetch(`/api/narvi/deal-sticks?${qs}`);
  if (!r.ok) {
    throw new Error(`narvi deal sticks failed: ${r.status}`);
  }
  return (await r.json()) as NarviDealSticks;
}
