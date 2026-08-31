import { useState } from "react";

import { DealPolygonsModal } from "../components/DealPolygonsModal";
import { NarviSticksModal } from "../components/NarviSticksModal";
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
  const showBasementFaults = useMapStore((s) => s.showBasementFaults);
  const setShowBasementFaults = useMapStore((s) => s.setShowBasementFaults);
  const showSnfFaults = useMapStore((s) => s.showSnfFaults);
  const setShowSnfFaults = useMapStore((s) => s.setShowSnfFaults);
  const dealPolygons = useMapStore((s) => s.dealPolygons);
  const showNarviSticks = useMapStore((s) => s.showNarviSticks);
  const setShowNarviSticks = useMapStore((s) => s.setShowNarviSticks);
  const narviDealIds = useMapStore((s) => s.narviDealIds);
  const narviSticks = useMapStore((s) => s.narviSticks);
  const [dealModalOpen, setDealModalOpen] = useState(false);
  const [narviModalOpen, setNarviModalOpen] = useState(false);

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
        <label
          className="chk-inline"
          title="Basement-rooted fault traces — Horne et al. 2022 (BEG), top-Ellenburger intersection"
        >
          <input
            type="checkbox"
            checked={showBasementFaults}
            onChange={(e) => setShowBasementFaults(e.target.checked)}
          />
          Bsmt faults
        </label>
        <label
          className="chk-inline"
          title="Shallow normal fault traces — Horne 2022 (BEG)"
        >
          <input
            type="checkbox"
            checked={showSnfFaults}
            onChange={(e) => setShowSnfFaults(e.target.checked)}
          />
          SNF
        </label>
      </div>
      <div className="toolbar-group">
        <span className="toolbar-label">Acreage:</span>
        <span className="muted">
          {polygonCount === 0
            ? "no shapefile uploaded"
            : `${polygonCount} polygon${polygonCount === 1 ? "" : "s"}`}
        </span>
        <button
          type="button"
          className="tb-btn"
          onClick={() => setDealModalOpen(true)}
          title="Upload shapefiles, toggle each on/off, or delete them"
        >
          Manage…
        </button>
      </div>
      <div className="toolbar-group">
        <span className="toolbar-label">Narvi:</span>
        <label
          className="chk-inline"
          title={
            narviDealIds.length === 0
              ? "Pick narvi deals first (Planned…)"
              : "Show / hide the dashed planned sticks"
          }
        >
          <input
            type="checkbox"
            checked={showNarviSticks}
            disabled={narviDealIds.length === 0}
            onChange={(e) => setShowNarviSticks(e.target.checked)}
          />
          Sticks
        </label>
        <span className="muted">
          {narviDealIds.length === 0
            ? "no deals selected"
            : `${narviDealIds.length} deal${narviDealIds.length === 1 ? "" : "s"}${
                narviSticks ? ` · ${narviSticks.wells.length} wells` : ""
              }`}
        </span>
        <button
          type="button"
          className="tb-btn"
          onClick={() => setNarviModalOpen(true)}
          title="Pick a narvi deal and toggle its benches"
        >
          Planned…
        </button>
      </div>
      {dealModalOpen && (
        <DealPolygonsModal onClose={() => setDealModalOpen(false)} />
      )}
      {narviModalOpen && (
        <NarviSticksModal onClose={() => setNarviModalOpen(false)} />
      )}
    </div>
  );
}
