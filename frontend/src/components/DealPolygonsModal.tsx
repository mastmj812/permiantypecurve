// Admin modal for deal-acreage polygons. Upload a shapefile (.zip),
// assign each parsed polygon to a Deal via dropdown, or delete.
//
// State source of truth is the backend; this component re-fetches
// after every mutation so the per-deal toggles in MapToolbar (which
// read from mapStore.dealPolygons) reflect the change as soon as it
// lands.

import { useCallback, useEffect, useRef, useState } from "react";

import { type DealSummary, listDeals } from "../api/deals";
import {
  type DealPolygonRow,
  deleteDealPolygon,
  fetchDealPolygonGeoJSON,
  fetchDealPolygons,
  patchDealPolygon,
  uploadShapefile,
} from "../api/dealPolygons";
import { useMapStore } from "../store/mapStore";

interface Props {
  onClose: () => void;
}

export function DealPolygonsModal({ onClose }: Props) {
  const [polygons, setPolygons] = useState<DealPolygonRow[]>([]);
  const [deals, setDeals] = useState<DealSummary[]>([]);
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
      console.warn("failed to refresh deal polygons in store", e);
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [polys, dealList] = await Promise.all([fetchDealPolygons(), listDeals()]);
      setPolygons(polys);
      setDeals(dealList);
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

  async function handleAssign(polygonId: string, dealId: string | null) {
    try {
      await patchDealPolygon(polygonId, dealId);
      await refresh();
      await refreshStore();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
          <strong>Deal acreage polygons</strong>
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
              .prj). Each feature becomes one row; assign to a deal below.
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
                <th>Deal</th>
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
                    <select
                      value={p.deal_id ?? ""}
                      onChange={(e) =>
                        handleAssign(p.id, e.target.value || null)
                      }
                    >
                      <option value="">— Unassigned —</option>
                      {deals.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </select>
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
                  <td colSpan={5} className="muted" style={{ textAlign: "center" }}>
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
