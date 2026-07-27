// Dossier scenario map — plan view of one narvi scenario: parcel AOI,
// producing legs colored by formation_blueox, U-turn arcs in gray, on
// the same basemap + blocks/sections overlays as the slide map. Like
// SlideMap it stays live (drag / scroll-zoom to frame the shot) and
// refreshes a hidden snapshot <img> on every idle — the PPTX capture
// reads the img, so whatever is framed at export time is what ships.

import { useEffect, useRef, useState } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { ACREAGE_COLOR } from "../../api/dealPolygons";
import type { NarviWellGeo } from "../../api/narvi";
import { colorForFormation } from "../../map/formations";
import {
  authedTransformRequest,
  buildStyle,
  loadBlocks,
  loadSections,
  registerPmtilesProtocol,
} from "../slide/mapShared";
import { useNearViewport } from "../slide/useNearViewport";

interface Props {
  aoiGeojson: string | null;
  wells: NarviWellGeo[];
  width: number;
  height: number;
  // Lazy mount: keep the live MapLibre instance only while the panel is
  // near the viewport; off-screen the map is torn down (releasing its
  // WebGL context) and the last idle snapshot shows in its place. The
  // dossier sets this — a dozen scenario sections of live maps blow the
  // browser's WebGL-context cap and OOM the tab.
  lazy?: boolean;
  // Per-well leg color override. Default is formation coloring; the
  // dossier's curve-assignment views pass a zone→curve palette instead.
  // Load-time input like the wells themselves — changing it after
  // mount does not restyle a live map.
  colorForWell?: (w: NarviWellGeo) => string;
  // Perpendicular screen-px offset per well (MapLibre line-offset).
  // Vertically-stacked benches often share IDENTICAL plan-view
  // laterals (Novi stacks e.g. BS2_S under BS3_C on one stick), so the
  // later-drawn zone paints over the other — the assignment overview
  // fans them apart a few px per zone so every color stays visible.
  // Cosmetic: positions shift by the offset at every zoom; leave unset
  // on maps meant to be spatially faithful.
  offsetForWell?: (w: NarviWellGeo) => number;
}

const AOI_SOURCE = "dossier-aoi";
const LEGS_SOURCE = "dossier-legs";
const TURNS_SOURCE = "dossier-turns";

type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;

function parseGeom(geojson: string | null): GeoJSON.Geometry | null {
  if (!geojson) return null;
  try {
    return JSON.parse(geojson) as GeoJSON.Geometry;
  } catch {
    return null;
  }
}

function collectCoords(geom: GeoJSON.Geometry, into: Array<[number, number]>): void {
  if (geom.type === "GeometryCollection") {
    for (const g of geom.geometries) collectCoords(g, into);
    return;
  }
  const walk = (c: unknown): void => {
    if (!Array.isArray(c)) return;
    if (typeof c[0] === "number" && typeof c[1] === "number") {
      into.push([c[0], c[1]]);
      return;
    }
    for (const child of c) walk(child);
  };
  walk(geom.coordinates);
}

export function ScenarioSlideMap({
  aoiGeojson,
  wells,
  width,
  height,
  lazy = false,
  colorForWell,
  offsetForWell,
}: Props) {
  const outerRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  // Camera survives lazy teardown so a framed shot isn't lost when the
  // panel scrolls away and back (the snapshot img covers the interim).
  const cameraRef = useRef<{ center: [number, number]; zoom: number } | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const live = useNearViewport(outerRef, lazy);

  useEffect(() => {
    if (!live || !containerRef.current || mapRef.current) return;
    registerPmtilesProtocol();

    const cam = cameraRef.current;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(),
      center: cam?.center ?? [-102.5, 32.0],
      zoom: cam?.zoom ?? 6,
      minZoom: 3,
      maxZoom: 16,
      attributionControl: false,
      transformRequest: authedTransformRequest,
      // Required for getCanvas().toDataURL() to return non-blank pixels.
      preserveDrawingBuffer: true,
    });

    const setup = () => {
      if (map.getSource(LEGS_SOURCE)) return;

      const aoi = parseGeom(aoiGeojson);
      const legFeatures: Feature[] = [];
      const turnFeatures: Feature[] = [];
      for (const w of wells) {
        const legs = parseGeom(w.legs_geojson);
        if (legs) {
          legFeatures.push({
            type: "Feature",
            geometry: legs,
            properties: {
              color: colorForWell ? colorForWell(w) : colorForFormation(w.formation),
              category: w.category,
              offset: offsetForWell ? offsetForWell(w) : 0,
            },
          });
        }
        const turn = parseGeom(w.turn_geojson);
        if (turn) {
          turnFeatures.push({ type: "Feature", geometry: turn, properties: {} });
        }
      }

      if (aoi) {
        map.addSource(AOI_SOURCE, {
          type: "geojson",
          data: { type: "Feature", geometry: aoi, properties: {} },
        });
        map.addLayer({
          id: `${AOI_SOURCE}-fill`,
          type: "fill",
          source: AOI_SOURCE,
          paint: { "fill-color": ACREAGE_COLOR, "fill-opacity": 0.1 },
        });
        map.addLayer({
          id: `${AOI_SOURCE}-line`,
          type: "line",
          source: AOI_SOURCE,
          paint: {
            "line-color": ACREAGE_COLOR,
            "line-width": 2.2,
            "line-opacity": 0.95,
          },
        });
      }
      map.addSource(TURNS_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: turnFeatures },
      });
      map.addLayer({
        id: `${TURNS_SOURCE}-line`,
        type: "line",
        source: TURNS_SOURCE,
        paint: {
          "line-color": "#6b7280",
          "line-width": 1.4,
          "line-dasharray": [2, 2],
          "line-opacity": 0.8,
        },
      });
      map.addSource(LEGS_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: legFeatures },
      });
      map.addLayer({
        id: `${LEGS_SOURCE}-line`,
        type: "line",
        source: LEGS_SOURCE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          // Screen-px fan-out for coincident stacked-bench laterals
          // (0 everywhere except the curve-assignment overview).
          "line-offset": ["get", "offset"],
          // ["zoom"] interpolate stays OUTERMOST (nesting silently
          // breaks the layer); PDP producers read muted vs planned.
          "line-width": [
            "interpolate", ["linear"], ["zoom"],
            8, 1.2,
            11, 2.6,
            14, 4.5,
          ],
          "line-opacity": [
            "case", ["==", ["get", "category"], "PDP"], 0.45, 0.95,
          ],
        },
      });

      // Fit to the AOI + legs — but never clobber a restored camera
      // (the user's framing from before a lazy teardown).
      const coords: Array<[number, number]> = [];
      if (aoi) collectCoords(aoi, coords);
      for (const f of legFeatures) collectCoords(f.geometry, coords);
      if (!cam && coords.length > 0) {
        const first = coords[0]!;
        const b = new maplibregl.LngLatBounds(first, first);
        for (const c of coords) b.extend(c);
        map.fitBounds(b, { padding: 32, duration: 0, maxZoom: 14 });
      }

      void Promise.allSettled([loadBlocks(map), loadSections(map)]).then(() => {
        // Scenario geometry stays on top of the survey-grid overlays.
        for (const id of [
          `${AOI_SOURCE}-fill`, `${AOI_SOURCE}-line`,
          `${TURNS_SOURCE}-line`, `${LEGS_SOURCE}-line`,
        ]) {
          if (map.getLayer(id)) map.moveLayer(id);
        }
        const refresh = () => {
          try {
            setSnapshot(map.getCanvas().toDataURL("image/png"));
          } catch (e) {
            console.error("scenario map snapshot failed", e);
          }
        };
        map.on("idle", refresh);
        map.triggerRepaint();
      });
    };

    map.on("load", setup);
    map.on("styledata", setup);

    mapRef.current = map;
    return () => {
      cameraRef.current = {
        center: map.getCenter().toArray(),
        zoom: map.getZoom(),
      };
      map.remove();
      mapRef.current = null;
    };
    // Scenario geometry is a load-time input, not live state; `live`
    // alone drives (lazy) mount/teardown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  return (
    <div ref={outerRef} className="slide-map" style={{ width, height, position: "relative" }}>
      <div ref={containerRef} className="slide-map-canvas" style={{ width, height }} />
      {snapshot && (
        <img
          src={snapshot}
          alt="Scenario plan view"
          className="slide-map-img"
          style={{
            width,
            height,
            position: "absolute",
            top: 0,
            left: 0,
            objectFit: "contain",
            // Torn-down lazy panel: show the last snapshot in place of
            // the (removed) live canvas. CSS hides this img otherwise.
            display: live ? undefined : "block",
          }}
        />
      )}
    </div>
  );
}
