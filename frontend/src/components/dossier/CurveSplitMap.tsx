// Dossier type-curve split map — one formation whose proposed wells take
// MORE THAN ONE type curve (e.g. bro_time WCB_2 West / East). Each curve
// gets one colour: its proposed wells DASHED, the wells that built it
// SOLID, so the map answers "where are our wells, which curve does each
// take, and which offsets built each curve". Formations with a single
// curve get no split map (it would add nothing).
//
// Spatially faithful (no line-offset fan-out): one formation per map, so
// there are no stacked-bench laterals to separate. Live like the other
// dossier maps; the legend is burned into the snapshot.

import { useEffect, useRef, useState } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { DossierCohortWell, DossierStick } from "../../api/deals";
import {
  authedTransformRequest,
  buildStyle,
  loadBlocks,
  loadSections,
  registerPmtilesProtocol,
} from "../slide/mapShared";
import { useNearViewport } from "../slide/useNearViewport";
import { composeSnapshot } from "./mapLegend";

export interface SplitGroup {
  label: string; // "WCB_2 West — broTime_wcb2_w"
  color: string;
  sticks: DossierStick[]; // this formation's proposed wells in the zone
  cohort: DossierCohortWell[]; // the wells that built the zone's curve
}

interface Props {
  groups: SplitGroup[];
  aois: string[];
  width: number;
  height: number;
  lazy?: boolean;
}

type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;

export function CurveSplitMap({ groups, aois, width, height, lazy = false }: Props) {
  const outerRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
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
      preserveDrawingBuffer: true,
    });

    const setup = () => {
      if (map.getSource("split-sticks")) return;
      const sticks: Feature[] = [];
      const wells: Feature[] = [];
      const coords: Array<[number, number]> = [];
      for (const g of groups) {
        for (const s of g.sticks) {
          for (const [hx, hy, tx, ty] of s.legs_lonlat as Array<[number, number, number, number]>) {
            sticks.push({
              type: "Feature",
              geometry: { type: "LineString", coordinates: [[hx, hy], [tx, ty]] },
              properties: { color: g.color },
            });
            coords.push([hx, hy], [tx, ty]);
          }
        }
        for (const w of g.cohort) {
          if (w.coords.length < 2) continue;
          wells.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: w.coords },
            properties: { color: g.color },
          });
          for (const c of w.coords) coords.push([c[0]!, c[1]!]);
        }
      }
      const aoiFeatures: Feature[] = [];
      for (const a of aois) {
        try {
          aoiFeatures.push({ type: "Feature", geometry: JSON.parse(a) as GeoJSON.Geometry, properties: {} });
        } catch {
          // unparseable AOI: skip
        }
      }
      const fc = (features: Feature[]) => ({ type: "FeatureCollection" as const, features });
      map.addSource("split-aoi", { type: "geojson", data: fc(aoiFeatures) });
      map.addSource("split-wells", { type: "geojson", data: fc(wells) });
      map.addSource("split-sticks", { type: "geojson", data: fc(sticks) });
      const layerIds: string[] = [];
      const add = (layer: maplibregl.LayerSpecification) => {
        map.addLayer(layer);
        layerIds.push(layer.id);
      };
      add({
        id: "split-aoi-line", type: "line", source: "split-aoi",
        paint: { "line-color": "#6b7280", "line-width": 1.4, "line-opacity": 0.8 },
      });
      add({
        id: "split-wells-casing", type: "line", source: "split-wells",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#374151", "line-width": 4.6, "line-opacity": 0.5 },
      });
      add({
        id: "split-wells", type: "line", source: "split-wells",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": ["get", "color"], "line-width": 3 },
      });
      add({
        id: "split-sticks", type: "line", source: "split-sticks",
        layout: { "line-cap": "butt" },
        paint: { "line-color": ["get", "color"], "line-width": 3.4, "line-dasharray": [2, 1.2] },
      });
      if (!cam && coords.length > 0) {
        const b = new maplibregl.LngLatBounds(coords[0], coords[0]);
        for (const c of coords) b.extend(c);
        map.fitBounds(b, { padding: 36, duration: 0, maxZoom: 14 });
      }
      const legend = {
        rows: groups.flatMap((g) => [
          { color: g.color, label: `${g.label}: ${g.sticks.length} proposed`, kind: "dash" as const },
          { color: g.color, label: `${g.label}: ${g.cohort.length} curve wells`, kind: "line" as const },
        ]),
      };
      void Promise.allSettled([loadBlocks(map), loadSections(map)]).then(() => {
        for (const id of layerIds) if (map.getLayer(id)) map.moveLayer(id);
        const refresh = () => {
          try {
            setSnapshot(composeSnapshot(map.getCanvas(), width, legend));
          } catch (e) {
            console.error("curve split map snapshot failed", e);
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
      cameraRef.current = { center: map.getCenter().toArray(), zoom: map.getZoom() };
      map.remove();
      mapRef.current = null;
    };
    // Load-time inputs; `live` alone drives (lazy) mount/teardown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  return (
    <div ref={outerRef} className="slide-map" style={{ width, height, position: "relative" }}>
      <div ref={containerRef} className="slide-map-canvas" style={{ width, height }} />
      {snapshot && (
        <img
          src={snapshot}
          alt="Type curve split"
          className="slide-map-img"
          style={{
            width,
            height,
            position: "absolute",
            top: 0,
            left: 0,
            objectFit: "contain",
            display: live ? undefined : "block",
          }}
        />
      )}
    </div>
  );
}
