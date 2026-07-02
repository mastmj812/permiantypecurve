// Acreage-polygon client. Mirrors backend/app/api/deal_polygons.py.
//
// Uploaded shapefiles are just displayed — there's no deal assignment.
// The backend still returns deal_id/color/visibility_key on the GeoJSON
// (nullable, unused here); the frontend renders every polygon with a
// single ACREAGE_COLOR and one show/hide toggle.

import { apiFetch } from "./auth";

// Shared fill/outline color for all uploaded acreage polygons. Kept in
// one place so the Map tab, Review tab, and slide export agree.
export const ACREAGE_COLOR = "#7c3aed";

export interface DealPolygonRow {
  id: string;
  name: string;
  attributes: Record<string, unknown>;
  source_file: string | null;
}

export interface DealPolygonGeoJSONFeature {
  type: "Feature";
  id: string;
  geometry: GeoJSON.MultiPolygon | GeoJSON.Polygon;
  properties: {
    id: string;
    name: string;
  };
}

export interface DealPolygonGeoJSON {
  type: "FeatureCollection";
  features: DealPolygonGeoJSONFeature[];
}

export interface UploadShapefileResponse {
  inserted: number;
  polygon_ids: string[];
  source_epsg: number | null;
  source_file: string;
}

export async function fetchDealPolygons(): Promise<DealPolygonRow[]> {
  const r = await apiFetch("/api/deals/polygons");
  if (!r.ok) throw new Error(`list deal polygons failed: ${r.status}`);
  return (await r.json()) as DealPolygonRow[];
}

export async function fetchDealPolygonGeoJSON(): Promise<DealPolygonGeoJSON> {
  const r = await apiFetch("/api/deals/polygons.geojson");
  if (!r.ok) throw new Error(`deal polygons geojson failed: ${r.status}`);
  return (await r.json()) as DealPolygonGeoJSON;
}

export async function uploadShapefile(file: File): Promise<UploadShapefileResponse> {
  const form = new FormData();
  form.append("file", file);
  const r = await apiFetch("/api/deals/polygons/upload-shapefile", {
    method: "POST",
    body: form,
  });
  if (!r.ok) {
    const body = await r.text().catch(() => "");
    throw new Error(`upload shapefile failed: ${r.status} ${body}`);
  }
  return (await r.json()) as UploadShapefileResponse;
}

export async function deleteDealPolygon(id: string): Promise<void> {
  const r = await apiFetch(`/api/deals/polygons/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`delete deal polygon failed: ${r.status}`);
}
