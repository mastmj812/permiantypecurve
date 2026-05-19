import { apiFetch } from "./auth";
import type {
  FilterFacets,
  FilterSpec,
  OperatorMatch,
  SelectResponse,
  SelectionSummary,
  WellDetail,
} from "./types";

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
  // api14 allow-list. 14 digits each — 500 of them = ~7.5 KB of query
  // string, still inside typical proxy buffer limits. Beyond that the
  // tile URL risks getting rejected; the FilterPanel input warns at
  // 500 to keep users out of that zone.
  if (spec.api14s.length) params.set("api14s", spec.api14s.join(","));
  return params.toString();
}

export function tileUrlTemplate(spec: FilterSpec): string {
  const q = filterSpecToQuery(spec);
  const suffix = q ? `?${q}` : "";
  return `/api/wells/tiles/{z}/{x}/{y}.mvt${suffix}`;
}

export async function fetchWellDetail(api14: string): Promise<WellDetail> {
  const r = await apiFetch(`/api/wells/${api14}`);
  if (!r.ok) throw new Error(`well lookup failed: ${r.status}`);
  return (await r.json()) as WellDetail;
}

export async function fetchOperators(q: string): Promise<OperatorMatch[]> {
  const r = await apiFetch(`/api/wells/filters/operators?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error(`operator typeahead failed: ${r.status}`);
  return (await r.json()) as OperatorMatch[];
}

// Lightweight GeoJSON for the review-tab map. One LineString per well
// (the heel-to-bottomhole wellstick), with api14/formation/operator
// properties for client-side coloring/joining against forecasts.
export interface WellstickFeatureCollection {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: { type: "LineString"; coordinates: [number, number][] };
    properties: { api14: string; formation: string | null; operator: string | null };
  }>;
}

export async function fetchWellsticks(api14s: string[]): Promise<WellstickFeatureCollection> {
  if (api14s.length === 0) {
    return { type: "FeatureCollection", features: [] };
  }
  const r = await apiFetch(`/api/wells/wellsticks?api14s=${api14s.join(",")}`);
  if (!r.ok) throw new Error(`wellsticks fetch failed: ${r.status}`);
  return (await r.json()) as WellstickFeatureCollection;
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

export interface GeoJsonPolygon {
  type: "Polygon";
  coordinates: number[][][];
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

export async function summaryForApi14s(api14s: string[]): Promise<SelectionSummary> {
  const r = await apiFetch("/api/wells/summary", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ api14s }),
  });
  if (!r.ok) throw new Error(`summary failed: ${r.status}`);
  return (await r.json()) as SelectionSummary;
}
