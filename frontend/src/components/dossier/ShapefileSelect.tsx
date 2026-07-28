// Acreage-shapefile picker for the dossier preview header. A checkbox
// dropdown listing every uploaded shapefile (distinct source_file);
// unchecking one filters its polygons off the cohort maps live (the
// SlideMap applies the change via setFilter, so framed shots survive).
// Multi-select falls out of the underlying per-shapefile visibility
// map — single selection is just "none, then check one".

import { useEffect, useRef, useState } from "react";

interface Props {
  // Distinct source_file names, first-seen order (NO_SOURCE_FILE
  // stands in for legacy rows without a filename).
  options: string[];
  // Absent or true = visible — same convention as mapStore.dealVisibility.
  visibility: Record<string, boolean>;
  onChange: (sourceFile: string, visible: boolean) => void;
}

export function ShapefileSelect({ options, visibility, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  // Close on outside-click. Stays open while clicking inside the menu.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const shown = options.filter((o) => visibility[o] !== false).length;
  const label =
    shown === options.length
      ? `Acreage: all (${options.length})`
      : shown === 0
        ? "Acreage: none"
        : `Acreage: ${shown} of ${options.length}`;

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className="tb-btn"
        onClick={() => setOpen((v) => !v)}
        title="Choose which uploaded shapefiles draw on the cohort maps"
      >
        {label} ▾
      </button>
      {open && (
        <div className="cohort-switcher-menu dossier-shp-menu">
          <div style={{ display: "flex", gap: 10, padding: "2px 12px 4px" }}>
            <button
              type="button"
              className="link-btn"
              onClick={() => options.forEach((o) => onChange(o, true))}
            >
              all
            </button>
            <button
              type="button"
              className="link-btn"
              onClick={() => options.forEach((o) => onChange(o, false))}
            >
              none
            </button>
          </div>
          {options.map((o) => (
            <label key={o} className="chk-inline dossier-shp-item" title={o}>
              <input
                type="checkbox"
                checked={visibility[o] !== false}
                onChange={(e) => onChange(o, e.target.checked)}
              />
              <span className="cohort-switcher-name">{o}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
