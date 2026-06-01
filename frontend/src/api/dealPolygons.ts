// Deal-acreage polygon client. Mirrors backend/app/api/deal_polygons.py.

import { apiFetch } from "./auth";

export interface DealPolygonRow {
  id: string;
  deal_id: string | null;
  deal_name: string | null;
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
    deal_id: string | null;
    deal_name: string | null;
    name: string;
    color: string;
    // Single key keyed off deal_id (or "_unlinked"); the MapView
    // filter expression matches this against the visibility store.
    visibility_key: string;
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

export async function patchDealPolygon(
  id: string,
  deal_id: string | null,
): Promise<DealPolygonRow> {
  const r = await apiFetch(`/api/deals/polygons/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ deal_id }),
  });
  if (!r.ok) throw new Error(`patch deal polygon failed: ${r.status}`);
  return (await r.json()) as DealPolygonRow;
}

export async function deleteDealPolygon(id: string): Promise<void> {
  const r = await apiFetch(`/api/deals/polygons/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`delete deal polygon failed: ${r.status}`);
}
