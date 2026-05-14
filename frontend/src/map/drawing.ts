// In-house drawing tools for selection — lasso, box, click.
//
// Why not mapbox-gl-draw / terra-draw: the brief needs three primitives,
// nothing more. A dependency adds 100+ KB and its own state model; ~150
// lines of code here covers the requirement and keeps DOM/MapLibre event
// flow in one place.
//
// The drawer maintains a scratch GeoJSON source on the map for live preview
// and emits one of three callbacks when the user releases:
//   * onPolygon(polygon)  — lasso closes
//   * onBbox(bbox)        — box drag ends
//   * onClick(lonlat)     — click in click mode (no preview)

import type { GeoJSONSource, LngLat, Map as MlMap, MapMouseEvent } from "maplibre-gl";

import type { GeoJsonPolygon } from "../api/wells";
import type { DrawMode } from "../store/mapStore";

const SCRATCH_SOURCE_ID = "draw-scratch";
const SCRATCH_FILL_LAYER = "draw-scratch-fill";
const SCRATCH_LINE_LAYER = "draw-scratch-line";

const EMPTY_FC: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

export interface DrawingCallbacks {
  onPolygon: (polygon: GeoJsonPolygon) => void;
  onBbox: (bbox: [number, number, number, number]) => void;
  onClick: (lngLat: LngLat) => void;
}

export class DrawingController {
  private mode: DrawMode = "off";
  private points: [number, number][] = [];
  private dragStart: LngLat | null = null;
  private dragging = false;
  private installed = false;

  constructor(
    private readonly map: MlMap,
    private readonly cb: DrawingCallbacks,
  ) {}

  install(): void {
    if (this.installed) return;
    if (!this.map.getSource(SCRATCH_SOURCE_ID)) {
      this.map.addSource(SCRATCH_SOURCE_ID, { type: "geojson", data: EMPTY_FC });
      this.map.addLayer({
        id: SCRATCH_FILL_LAYER,
        type: "fill",
        source: SCRATCH_SOURCE_ID,
        paint: { "fill-color": "#facc15", "fill-opacity": 0.15 },
      });
      this.map.addLayer({
        id: SCRATCH_LINE_LAYER,
        type: "line",
        source: SCRATCH_SOURCE_ID,
        paint: { "line-color": "#facc15", "line-width": 2 },
      });
    }
    this.map.on("mousedown", this.onMouseDown);
    this.map.on("mousemove", this.onMouseMove);
    this.map.on("mouseup", this.onMouseUp);
    this.map.on("click", this.onClick);
    this.installed = true;
  }

  uninstall(): void {
    if (!this.installed) return;
    this.map.off("mousedown", this.onMouseDown);
    this.map.off("mousemove", this.onMouseMove);
    this.map.off("mouseup", this.onMouseUp);
    this.map.off("click", this.onClick);
    this.clearScratch();
    this.installed = false;
  }

  setMode(mode: DrawMode): void {
    this.mode = mode;
    this.points = [];
    this.dragStart = null;
    this.dragging = false;
    this.clearScratch();
    // Disable map panning while drawing — otherwise the canvas drags with us.
    if (mode === "lasso" || mode === "box") {
      this.map.dragPan.disable();
      this.map.getCanvas().style.cursor = "crosshair";
    } else {
      this.map.dragPan.enable();
      this.map.getCanvas().style.cursor = "";
    }
  }

  // -------- click mode --------
  private onClick = (e: MapMouseEvent): void => {
    if (this.mode !== "click") return;
    this.cb.onClick(e.lngLat);
  };

  // -------- lasso & box: shared mouse machinery --------
  private onMouseDown = (e: MapMouseEvent): void => {
    if (this.mode === "lasso") {
      this.dragging = true;
      this.points = [[e.lngLat.lng, e.lngLat.lat]];
    } else if (this.mode === "box") {
      this.dragging = true;
      this.dragStart = e.lngLat;
    }
  };

  private onMouseMove = (e: MapMouseEvent): void => {
    if (!this.dragging) return;
    if (this.mode === "lasso") {
      this.points.push([e.lngLat.lng, e.lngLat.lat]);
      this.renderLasso();
    } else if (this.mode === "box" && this.dragStart) {
      this.renderBox(this.dragStart, e.lngLat);
    }
  };

  private onMouseUp = (e: MapMouseEvent): void => {
    if (!this.dragging) return;
    this.dragging = false;
    if (this.mode === "lasso") {
      if (this.points.length >= 3) {
        const closed = [...this.points, this.points[0]!];
        const poly: GeoJsonPolygon = { type: "Polygon", coordinates: [closed] };
        this.cb.onPolygon(poly);
      }
      this.points = [];
    } else if (this.mode === "box" && this.dragStart) {
      const a = this.dragStart;
      const b = e.lngLat;
      const w = Math.min(a.lng, b.lng);
      const ee = Math.max(a.lng, b.lng);
      const s = Math.min(a.lat, b.lat);
      const n = Math.max(a.lat, b.lat);
      // Filter out accidental zero-size boxes (click-and-release).
      if (Math.abs(ee - w) > 1e-6 && Math.abs(n - s) > 1e-6) {
        this.cb.onBbox([w, s, ee, n]);
      }
      this.dragStart = null;
    }
    this.clearScratch();
  };

  // -------- scratch rendering --------
  private renderLasso(): void {
    const src = this.map.getSource(SCRATCH_SOURCE_ID) as GeoJSONSource | undefined;
    if (!src) return;
    const ring = [...this.points, this.points[0]!];
    src.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: { type: "Polygon", coordinates: [ring] },
        },
      ],
    });
  }

  private renderBox(a: LngLat, b: LngLat): void {
    const src = this.map.getSource(SCRATCH_SOURCE_ID) as GeoJSONSource | undefined;
    if (!src) return;
    const ring = [
      [a.lng, a.lat],
      [b.lng, a.lat],
      [b.lng, b.lat],
      [a.lng, b.lat],
      [a.lng, a.lat],
    ];
    src.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: { type: "Polygon", coordinates: [ring] },
        },
      ],
    });
  }

  private clearScratch(): void {
    const src = this.map.getSource(SCRATCH_SOURCE_ID) as GeoJSONSource | undefined;
    src?.setData(EMPTY_FC);
  }
}
