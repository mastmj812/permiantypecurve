import { useMemo, useState } from "react";

import { DealPolygonsModal } from "../components/DealPolygonsModal";
import { useMapStore, type DrawMode } from "../store/mapStore";

const MODES: { mode: DrawMode; label: string; hint: string }[] = [
  { mode: "off", label: "pan", hint: "Pan and zoom the map (default)" },
  { mode: "click", label: "click", hint: "Click a well to toggle in selection" },
  { mode: "box", label: "box", hint: "Drag a rectangle to select" },
  { mode: "lasso", label: "lasso", hint: "Freehand polygon select" },
];

export function MapToolbar() {
  const drawMode = useMapStore((s) => s.drawMode);
  const setDrawMode = useMapStore((s) => s.setDrawMode);
  const showWellsticks = useMapStore((s) => s.showWellsticks);
  const setShowWellsticks = useMapStore((s) => s.setShowWellsticks);
  const showBlocks = useMapStore((s) => s.showBlocks);
  const setShowBlocks = useMapStore((s) => s.setShowBlocks);
  const showSections = useMapStore((s) => s.showSections);
  const setShowSections = useMapStore((s) => s.setShowSections);
  const dealPolygons = useMapStore((s) => s.dealPolygons);
  const dealVisibility = useMapStore((s) => s.dealVisibility);
  const setDealVisibility = useMapStore((s) => s.setDealVisibility);
  const [dealModalOpen, setDealModalOpen] = useState(false);

  // Unique deal toggles, derived from the loaded polygon
  // FeatureCollection. Each distinct (visibility_key, label) pair
  // becomes one checkbox row. "_unlinked" sorts to the bottom.
  const dealToggles = useMemo(() => {
    if (!dealPolygons) return [] as Array<{ key: string; label: string }>;
    const seen = new Map<string, string>();
    for (const f of dealPolygons.features) {
      const key = f.properties.visibility_key;
      if (seen.has(key)) continue;
      seen.set(key, f.properties.deal_name ?? "Unassigned");
    }
    const out = Array.from(seen.entries()).map(([key, label]) => ({ key, label }));
    out.sort((a, b) => {
      if (a.key === "_unlinked") return 1;
      if (b.key === "_unlinked") return -1;
      return a.label.localeCompare(b.label);
    });
    return out;
  }, [dealPolygons]);

  return (
    <div className="map-toolbar">
      <div className="toolbar-group">
        <span className="toolbar-label">Select:</span>
        {MODES.map(({ mode, label, hint }) => (
          <button
            key={mode}
            type="button"
            title={hint}
            className={`tb-btn ${drawMode === mode ? "tb-active" : ""}`}
            onClick={() => setDrawMode(mode)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="toolbar-group">
        <label className="chk-inline">
          <input
            type="checkbox"
            checked={showWellsticks}
            onChange={(e) => setShowWellsticks(e.target.checked)}
          />
          Wellsticks
        </label>
        <label
          className="chk-inline"
          title="Block grid — labels render at zoom 8+"
        >
          <input
            type="checkbox"
            checked={showBlocks}
            onChange={(e) => setShowBlocks(e.target.checked)}
          />
          Blocks
        </label>
        <label
          className="chk-inline"
          title="Section grid — labels render at zoom 11+"
        >
          <input
            type="checkbox"
            checked={showSections}
            onChange={(e) => setShowSections(e.target.checked)}
          />
          Sections
        </label>
      </div>
      <div className="toolbar-group">
        <span className="toolbar-label">Deals:</span>
        {dealToggles.length === 0 && (
          <span className="muted" style={{ fontSize: 11 }}>
            no polygons uploaded
          </span>
        )}
        {dealToggles.map(({ key, label }) => (
          <label
            key={key}
            className="chk-inline"
            title={
              key === "_unlinked"
                ? "Polygons not yet assigned to a deal — link them in Manage…"
                : `Toggle ${label} acreage`
            }
          >
            <input
              type="checkbox"
              checked={dealVisibility[key] !== false}
              onChange={(e) => setDealVisibility(key, e.target.checked)}
            />
            {label}
          </label>
        ))}
        <button
          type="button"
          className="tb-btn"
          onClick={() => setDealModalOpen(true)}
          title="Upload a shapefile, assign polygons to deals, delete polygons"
        >
          Manage…
        </button>
      </div>
      {dealModalOpen && (
        <DealPolygonsModal onClose={() => setDealModalOpen(false)} />
      )}
    </div>
  );
}
