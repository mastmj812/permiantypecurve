import { useState } from "react";

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
  const showDealPolygons = useMapStore((s) => s.showDealPolygons);
  const setShowDealPolygons = useMapStore((s) => s.setShowDealPolygons);
  const [dealModalOpen, setDealModalOpen] = useState(false);

  const polygonCount = dealPolygons?.features.length ?? 0;

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
        <span className="toolbar-label">Acreage:</span>
        {polygonCount === 0 ? (
          <span className="muted" style={{ fontSize: 11 }}>
            no shapefile uploaded
          </span>
        ) : (
          <label className="chk-inline" title="Show / hide uploaded acreage polygons">
            <input
              type="checkbox"
              checked={showDealPolygons}
              onChange={(e) => setShowDealPolygons(e.target.checked)}
            />
            Show ({polygonCount})
          </label>
        )}
        <button
          type="button"
          className="tb-btn"
          onClick={() => setDealModalOpen(true)}
          title="Upload a shapefile or delete uploaded acreage polygons"
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
