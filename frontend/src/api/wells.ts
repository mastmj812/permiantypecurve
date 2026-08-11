import { apiFetch } from "./auth";
import type {
  FilterFacets,
  FilterSpec,
  GeoJsonPolygon,
  OperatorMatch,
  SelectResponse,
  SelectionSummary,
  WellDetail,
} from "./types";

// Canonical definition moved to types.ts (the provenance types need it
// without creating an import cycle); re-exported here for back-compat.
export type { GeoJsonPolygon } from "./types";

// FilterSpec → URL query string for the MVT tile source.
// Only non-default keys land in the URL so the cache key is compact.
export function filterSpecToQuery(spec: FilterSpec): string {
  const params = new URLSearchParams();
  if (spec.formations.length) params.set("formations", spec.formations.join(","));
  if (spec.operators.length) params.set("operators", spec.operators.join(","));
  if (spec.counties.length) params.set("counties", spec.counties.join(","));
  if (spec.statuses.length) params.set("statuses", spec.statuses.join(","));
  if (spec.first_prod_start) params.set("first_prod_start", spec.first_prod_start);
  if (spec.first_prod_end) params.set("first_prod_end", spec.first_prod_end);
  if (spec.lateral_min_ft != null) params.set("lateral_min_ft", String(spec.lateral_min_ft));
  if (spec.lateral_max_ft != null) params.set("lateral_max_ft", String(spec.lateral_max_ft));
  if (spec.spacing_min_ft != null) params.set("spacing_min_ft", String(spec.spacing_min_ft));
  if (spec.spacing_max_ft != null) params.set("spacing_max_ft", String(spec.spacing_max_ft));
  if (spec.well_name_contains) params.set("well_name_contains", spec.well_name_contains);
  // Only meaningful alongside a spacing bound; omitted otherwise to
  // keep the tile-cache key compact.
  if (
    spec.spacing_include_unbounded &&
    (spec.spacing_min_ft != null || spec.spacing_max_ft != null)
  ) {
    params.set("spacing_include_unbounded", "true");
  }
  // api10 allow-list. 14 digits each — 500 of them = ~7.5 KB of query
  // string, still inside typical proxy buffer limits. Beyond that the
  // tile URL risks getting rejected; the FilterPanel input warns at
  // 500 to keep users out of that zone.
  if (spec.api10s.length) params.set("api10s", spec.api10s.join(","));
  return params.toString();
}

export function tileUrlTemplate(
  spec: FilterSpec,
  opts?: { alwaysLines?: boolean },
): string {
  const q = filterSpecToQuery(spec);
  // The slide-export map passes alwaysLines so wells render as sticks
  // at every zoom (the backend's default returns surface points below
  // z=9, which looks like dots on the slide for spread-out cohorts).
  const params = new URLSearchParams(q);
  if (opts?.alwaysLines) params.set("always_lines", "1");
  const qs = params.toString();
  const suffix = qs ? `?${qs}` : "";
  return `/api/wells/tiles/{z}/{x}/{y}.mvt${suffix}`;
}

export async function fetchWellDetail(api10: string): Promise<WellDetail> {
  const r = await apiFetch(`/api/wells/${api10}`);
  if (!r.ok) throw new Error(`well lookup failed: ${r.status}`);
  return (await r.json()) as WellDetail;
}

export async function fetchOperators(q: string): Promise<OperatorMatch[]> {
  const r = await apiFetch(`/api/wells/filters/operators?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error(`operator typeahead failed: ${r.status}`);
  return (await r.json()) as OperatorMatch[];
}

// Lightweight GeoJSON for the review-tab map. One LineString per well
// (the heel-to-bottomhole wellstick), with api10/formation/operator
// properties for client-side coloring/joining against forecasts.
export interface WellstickFeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "LineString"; coordinates: [number, number][] };
    properties: {
      api10: string;
      formation: string | null;
      formation_blueox: string | null;
      basin_blueox: string | null;
      operator: string | null;
    };
  }>;
}

export async function fetchWellsticks(api10s: string[]): Promise<WellstickFeatureCollection> {
  if (api10s.length === 0) {
    return { type: "FeatureCollection", features: [] };
  }
  const r = await apiFetch(`/api/wells/wellsticks?api10s=${api10s.join(",")}`);
  if (!r.ok) throw new Error(`wellsticks fetch failed: ${r.status}`);
  return (await r.json()) as WellstickFeatureCollection;
}

// Lightweight per-well bundle for the gun-barrel inspect modal. Mirrors
// backend WellDetailLite — just the fields the cross-section + tooltip
// render. Heavier `fetchWellDetail` stays for the single-well modal on
// the map tab.
export interface WellDetailLite {
  api10: string;
  name: string | null;
  formation: string | null;
  formation_blueox: string | null;
  basin_blueox: string | null;
  operator: string | null;
  lateral_ft: number | null;
  tvd_ft: number | null;
  sh_lat: number | null;
  sh_lon: number | null;
  bh_lat: number | null;
  bh_lon: number | null;
  first_prod_date: string | null;
  vintage_year: number | null;
  county: string | null;
  novi_oil_eur: number | null;
}

export async function fetchWellDetails(api10s: string[]): Promise<WellDetailLite[]> {
  if (api10s.length === 0) return [];
  const r = await apiFetch(`/api/wells/details?api10s=${api10s.join(",")}`);
  if (!r.ok) throw new Error(`well details fetch failed: ${r.status}`);
  return (await r.json()) as WellDetailLite[];
}

// Unfiltered neighbors of a staged set — the gun-barrel's greyed context
// wells (any formation, any status; the map filters deliberately don't
// apply). Display-only: context wells never stage, never persist.
export async function fetchContextWells(
  api10s: string[],
  opts?: { radiusFt?: number; limit?: number },
): Promise<WellDetailLite[]> {
  if (api10s.length === 0) return [];
  const r = await apiFetch("/api/wells/context", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      api10s,
      radius_ft: opts?.radiusFt ?? 3000,
      limit: opts?.limit ?? 300,
    }),
  });
  if (!r.ok) throw new Error(`context wells fetch failed: ${r.status}`);
  return (await r.json()) as WellDetailLite[];
}

export async function fetchFacets(spec?: FilterSpec): Promise<FilterFacets> {
  // Pass the current filters so the backend can compute per-facet counts
  // under "all filters except this facet's own column". The endpoint
  // ignores any filter keys that match the facet being counted, so we
  // don't have to strip formation here — just hand it the full spec.
  const q = spec ? filterSpecToQuery(spec) : "";
  const url = q ? `/api/wells/filters/facets?${q}` : "/api/wells/filters/facets";
  const r = await apiFetch(url);
  if (!r.ok) throw new Error(`facets failed: ${r.status}`);
  return (await r.json()) as FilterFacets;
}

export async function selectWellsSpatial(args: {
  polygon?: GeoJsonPolygon;
  bbox?: [number, number, number, number];
  filters: FilterSpec;
}): Promise<SelectResponse> {
  const r = await apiFetch("/api/wells/select", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      polygon: args.polygon ?? null,
      bbox: args.bbox ?? null,
      filters: args.filters,
    }),
  });
  if (!r.ok) throw new Error(`selection failed: ${r.status}`);
  return (await r.json()) as SelectResponse;
}

export async function summaryForApi10s(api10s: string[]): Promise<SelectionSummary> {
  const r = await apiFetch("/api/wells/summary", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ api10s }),
  });
  if (!r.ok) throw new Error(`summary failed: ${r.status}`);
  return (await r.json()) as SelectionSummary;
}
