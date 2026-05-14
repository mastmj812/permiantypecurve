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
  const showPlss = useMapStore((s) => s.showPlss);
  const setShowPlss = useMapStore((s) => s.setShowPlss);

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
        <label className="chk-inline">
          <input
            type="checkbox"
            checked={showPlss}
            onChange={(e) => setShowPlss(e.target.checked)}
          />
          PLSS
        </label>
      </div>
    </div>
  );
}
