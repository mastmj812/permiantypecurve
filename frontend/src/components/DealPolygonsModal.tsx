// Admin modal for acreage polygons. Upload a shapefile (.zip) to
// display it on the map, delete polygons, and toggle each uploaded
// shapefile on/off. There's no deal assignment — acreage is just shown.
//
// Polygons are grouped by their source shapefile. Each group has a
// show/hide checkbox wired to mapStore.dealVisibility (keyed by
// source_file), which the Map tab reads to filter the acreage layer.
//
// State source of truth is the backend; this component re-fetches
// after every mutation so the map (which reads from
// mapStore.dealPolygons) reflects the change as soon as it lands.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  type DealPolygonRow,
  NO_SOURCE_FILE,
  deleteDealPolygon,
  deleteShapefile,
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

  const dealVisibility = useMapStore((s) => s.dealVisibility);
  const setDealVisibility = useMapStore((s) => s.setDealVisibility);
  const setDealVisibilityAll = useMapStore((s) => s.setDealVisibilityAll);

  // Group polygons by their source shapefile so each upload gets one
  // visibility toggle. Preserves first-seen order.
  const groups = useMemo(() => {
    const m = new Map<string, DealPolygonRow[]>();
    for (const p of polygons) {
      const key = p.source_file ?? NO_SOURCE_FILE;
      const arr = m.get(key);
      if (arr) arr.push(p);
      else m.set(key, [p]);
    }
    return Array.from(m.entries());
  }, [polygons]);

  const sourceFiles = useMemo(() => groups.map(([f]) => f), [groups]);
  const visibleCount = sourceFiles.filter(
    (f) => dealVisibility[f] !== false,
  ).length;

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
    void refresh();
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

  async function handleDeleteGroup(sourceFile: string, count: number) {
    if (
      !confirm(
        `Delete all ${count} polygon${count === 1 ? "" : "s"} from "${sourceFile}"? This cannot be undone.`,
      )
    ) {
      return;
    }
    try {
      await deleteShapefile(sourceFile);
      await refresh();
      await refreshStore();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  // Portal to <body>: this modal is rendered from inside .map-toolbar,
  // which has a CSS transform. A transformed ancestor becomes the
  // containing block for position:fixed descendants, which would trap
  // the fixed backdrop inside the toolbar strip instead of covering the
  // viewport. Portaling escapes the transform.
  return createPortal(
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
              accept=".zip,.gpkg"
              disabled={uploading}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleUpload(f);
              }}
            />
            <span className="muted">
              Upload a shapefile .zip (.shp + .dbf + .prj) or a GeoPackage
              (.gpkg). Each feature becomes one polygon, shown on the Map and
              Review tabs. Re-uploading a file with the same name replaces its
              polygons.
            </span>
          </div>

          {error && (
            <div className="alert alert-error" style={{ marginBottom: 8 }}>
              {error}
            </div>
          )}
          {loading && <p className="muted">loading…</p>}

          {!loading && groups.length === 0 && (
            <p className="muted" style={{ textAlign: "center" }}>
              No polygons yet — upload a shapefile or GeoPackage to get started.
            </p>
          )}

          {groups.length > 1 && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 8,
              }}
            >
              <span className="muted">
                {visibleCount} of {groups.length} shapefiles shown
              </span>
              <button
                type="button"
                className="link-btn"
                disabled={visibleCount === groups.length}
                onClick={() => setDealVisibilityAll(sourceFiles, true)}
                title="Show every shapefile on the map"
              >
                select all
              </button>
              <button
                type="button"
                className="link-btn"
                disabled={visibleCount === 0}
                onClick={() => setDealVisibilityAll(sourceFiles, false)}
                title="Hide every shapefile on the map"
              >
                deselect all
              </button>
            </div>
          )}

          {groups.map(([sourceFile, rows]) => {
            // Absent key = visible (default on); the map reads the same
            // store slice and filters this shapefile out when unchecked.
            const visible = dealVisibility[sourceFile] !== false;
            return (
              <div
                key={sourceFile}
                style={{
                  marginBottom: 14,
                  border: "1px solid #e5e7eb",
                  borderRadius: 6,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "8px 10px",
                    background: "#fafafa",
                    borderBottom: "1px solid #f3f4f6",
                  }}
                >
                  <label
                    className="chk-inline"
                    title="Show / hide this shapefile on the map"
                  >
                    <input
                      type="checkbox"
                      checked={visible}
                      onChange={(e) =>
                        setDealVisibility(sourceFile, e.target.checked)
                      }
                    />
                    <strong>{sourceFile}</strong>
                  </label>
                  <span className="muted">
                    {rows.length} polygon{rows.length === 1 ? "" : "s"}
                    {!visible && " · hidden"}
                  </span>
                  {sourceFile !== NO_SOURCE_FILE && (
                    <button
                      type="button"
                      className="link-btn"
                      style={{ marginLeft: "auto" }}
                      onClick={() => void handleDeleteGroup(sourceFile, rows.length)}
                      title="Delete every polygon from this shapefile"
                    >
                      delete shapefile
                    </button>
                  )}
                </div>
                <table className="deal-polygon-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Attributes</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => (
                      <tr key={p.id}>
                        <td>{p.name}</td>
                        <td>
                          <pre
                            style={{
                              margin: 0,
                              fontSize: 12,
                              maxWidth: 320,
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
                            onClick={() => void handleDelete(p.id)}
                          >
                            delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}
