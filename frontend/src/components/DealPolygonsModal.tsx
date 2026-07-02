// Admin modal for acreage polygons. Upload a shapefile (.zip) to
// display it on the map, or delete an uploaded polygon. There's no
// deal assignment — uploaded acreage is just shown.
//
// State source of truth is the backend; this component re-fetches
// after every mutation so the map (which reads from
// mapStore.dealPolygons) reflects the change as soon as it lands.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  type DealPolygonRow,
  deleteDealPolygon,
  fetchDealPolygonGeoJSON,
  fetchDealPolygons,
  uploadShapefile,
} from "../api/dealPolygons";
import { useMapStore } from "../store/mapStore";

interface Props {
  onClose: () => void;
}

export function DealPolygonsModal({ onClose }: Props) {
  const [polygons, setPolygons] = useState<DealPolygonRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refreshStore = useCallback(async () => {
    // Push the latest GeoJSON into the store so MapView re-renders.
    try {
      const fc = await fetchDealPolygonGeoJSON();
      useMapStore.getState().setDealPolygons(fc);
    } catch (e) {
      console.warn("failed to refresh acreage polygons in store", e);
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const polys = await fetchDealPolygons();
      setPolygons(polys);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleUpload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const resp = await uploadShapefile(file);
      console.log("shapefile upload:", resp);
      await refresh();
      await refreshStore();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleDelete(polygonId: string) {
    if (!confirm("Delete this polygon? This cannot be undone.")) return;
    try {
      await deleteDealPolygon(polygonId);
      await refresh();
      await refreshStore();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 960 }}
      >
        <header className="modal-header">
          <strong>Acreage polygons</strong>
          <button type="button" className="link-btn" onClick={onClose}>
            close
          </button>
        </header>
        <div className="modal-body">
          <div className="toolbar-group" style={{ marginBottom: 12 }}>
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              disabled={uploading}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleUpload(f);
              }}
            />
            <span className="muted" style={{ fontSize: 11 }}>
              Upload a .zip containing the shapefile components (.shp + .dbf +
              .prj). Each feature becomes one polygon and is displayed on the
              Map and Review tabs.
            </span>
          </div>

          {error && (
            <div className="alert alert-error" style={{ marginBottom: 8 }}>
              {error}
            </div>
          )}
          {loading && <p className="muted">loading…</p>}

          <table className="deal-polygon-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Source file</th>
                <th>Attributes</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {polygons.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td>
                    <span className="muted" style={{ fontSize: 11 }}>
                      {p.source_file ?? "—"}
                    </span>
                  </td>
                  <td>
                    <pre
                      style={{
                        margin: 0,
                        fontSize: 10,
                        maxWidth: 280,
                        maxHeight: 80,
                        overflow: "auto",
                      }}
                    >
                      {JSON.stringify(p.attributes, null, 0)}
                    </pre>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => handleDelete(p.id)}
                    >
                      delete
                    </button>
                  </td>
                </tr>
              ))}
              {polygons.length === 0 && !loading && (
                <tr>
                  <td colSpan={4} className="muted" style={{ textAlign: "center" }}>
                    No polygons yet — upload a shapefile to get started.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
