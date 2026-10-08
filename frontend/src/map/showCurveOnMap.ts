// "Show on map" for a SAVED type curve. Cohorts are browser-only
// (cohortStore persists to localStorage), so opening a saved curve
// never put its wells on the Map tab — a curve saved by the deal-intake
// handoff, or opened on another machine, had nothing to inspect there.
// This rebuilds the cohort from the curve's own membership every time:
// the server's included_api10s is the source of truth, the browser
// cohort is just the working copy that drives the halo, Inspect and the
// gunbarrel.

import { fetchWellDetails, summaryForApi10s, type WellDetailLite } from "../api/wells";
import { useCohortStore } from "../store/cohortStore";
import { useMapStore } from "../store/mapStore";

export interface CurveForMap {
  name: string;
  included_api10s: string[];
  filter_spec?: Record<string, unknown>;
  provenance?: Record<string, unknown>;
}

export type Bounds = [number, number, number, number]; // [west, south, east, north]

// Benches the curve was built on: the formations recorded at draw time,
// else the saved filter's formations. [] = no bench filter (show all).
export function curveFormations(tc: CurveForMap): string[] {
  const fromProv = tc.provenance?.["formations"];
  if (Array.isArray(fromProv) && fromProv.length > 0) return fromProv.map(String);
  const fromFilter = tc.filter_spec?.["formations"];
  if (Array.isArray(fromFilter)) return fromFilter.map(String);
  return [];
}

// Bounding box over every well's surface AND bottom hole, so whole
// laterals land in view. null when no well has a location.
export function wellBounds(wells: WellDetailLite[]): Bounds | null {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const d of wells) {
    for (const [lon, lat] of [[d.sh_lon, d.sh_lat], [d.bh_lon, d.bh_lat]] as const) {
      if (lon == null || lat == null) continue;
      w = Math.min(w, lon);
      e = Math.max(e, lon);
      s = Math.min(s, lat);
      n = Math.max(n, lat);
    }
  }
  return Number.isFinite(w) ? [w, s, e, n] : null;
}

// Reuse the cohort already named after this curve (re-syncing its wells
// to the saved membership) rather than piling up duplicates on repeat
// clicks; otherwise create it. Returns the active cohort id.
export function activateCurveCohort(tc: CurveForMap): string {
  const store = useCohortStore.getState();
  const api10s = Array.from(new Set(tc.included_api10s));
  const existing = store.cohorts.find((c) => c.name === tc.name);
  if (existing) {
    const same =
      existing.api10s.length === api10s.length && existing.api10s.every((a) => api10s.includes(a));
    if (!same) store.replaceApi10s(existing.id, api10s);
    store.setActive(existing.id);
    return existing.id;
  }
  return store.createCohort({ name: tc.name, initial_api10s: api10s });
}

export async function showCurveOnMap(tc: CurveForMap): Promise<void> {
  const map = useMapStore.getState();
  const api10s = Array.from(new Set(tc.included_api10s));
  // Start from a clean filter on the curve's bench(es): a leftover
  // filter (another bench, a vintage window) would hide the very wells
  // we're about to highlight. An api10 allow-list is NOT used, so the
  // neighbours stay visible on the map for context.
  map.resetFilters();
  map.setFormations(curveFormations(tc));
  activateCurveCohort(tc);
  // Staged selection = the curve's wells, so Inspect (gunbarrel) is one click.
  map.setSelection(api10s, null);
  map.setCurrentPage("map");
  const [summary, details] = await Promise.allSettled([
    summaryForApi10s(api10s),
    fetchWellDetails(api10s),
  ]);
  if (summary.status === "fulfilled") {
    useMapStore.getState().setSelection(api10s, summary.value);
  }
  if (details.status === "fulfilled") {
    const b = wellBounds(details.value);
    if (b) useMapStore.getState().requestFit(b);
  }
}
