// Dossier zone support map — which planned sticks take this zone's type
// curve, and which wells build it. Ported from the deal-intake dossier's
// curve map (engineering_db dealintake/render/maps.py):
//
//   mode "support": the zone's sticks (zone colour, white casing), a thin
//     link from each scenario's sticks to every cohort well, and the
//     cohort wells drawn as their laterals coloured by anduin oil EUR/ft
//     (resolved per-well fit, raw 50-yr, unrisked; grey = no fit), value
//     printed at the midpoint.
//   mode "sticks": zoom on the zone's sticks — one always-on label per
//     scenario (name · n sticks · lateral range) and a per-stick label
//     (short name · lateral ft) laid along each stick, shown wherever
//     it fits (zoom in before exporting to bring them all up) — cohort
//     wells in view.
//
// Live like the other dossier maps (frame, then export); the legend is
// burned into the snapshot (composeSnapshot) so it reaches the deck.

import { useEffect, useRef, useState } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { DossierZone } from "../../api/deals";
import {
  authedTransformRequest,
  buildStyle,
  loadBlocks,
  loadSections,
  registerPmtilesProtocol,
} from "../slide/mapShared";
import { useNearViewport } from "../slide/useNearViewport";
import { NO_FIT_COLOR, VIRIDIS, composeSnapshot, viridisExpression } from "./mapLegend";

interface Props {
  zone: DossierZone;
  color: string; // the zone's colour (blueoxZones.zoneColor)
  aois: string[]; // scenario AOI GeoJSON strings for the zone's scenarios
  // "<deal_id>/<scenario_id>" -> narvi scenario name: group labels, and
  // stripped from stick names (narvi prefixes generated wells with it).
  scenarioNames: Record<string, string>;
  mode: "support" | "sticks";
  eurRange: { lo: number; hi: number }; // shared bbl/ft colour scale
  width: number;
  height: number;
  lazy?: boolean;
}

const MAX_VALUE_LABELS = 45; // beyond this the EUR labels bury the map

type Feature = GeoJSON.Feature<GeoJSON.Geometry, Record<string, unknown>>;

function midpoint(coords: number[][]): [number, number] | null {
  if (coords.length === 0) return null;
  if (coords.length % 2 === 1) {
    const c = coords[(coords.length - 1) / 2]!;
    return [c[0]!, c[1]!];
  }
  const a = coords[coords.length / 2 - 1]!;
  const b = coords[coords.length / 2]!;
  return [(a[0]! + b[0]!) / 2, (a[1]! + b[1]!) / 2];
}

export function ZoneSupportMap({
  zone,
  color,
  aois,
  scenarioNames,
  mode,
  eurRange,
  width,
  height,
  lazy = false,
}: Props) {
  const outerRef = useRef<HTMLDivElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const cameraRef = useRef<{ center: [number, number]; zoom: number } | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const live = useNearViewport(outerRef, lazy);
  const src = `zsm-${mode}`;

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
      if (map.getSource(`${src}-wells`)) return;

      // Planned sticks: one feature per producing leg, plus a label
      // point at each leg's midpoint rotated along it (text-rotate is
      // clockwise; the x-span is scaled by cos(lat) so the angle is true
      // on screen; kept upright).
      const stickFeatures: Feature[] = [];
      const stickLabelFeatures: Feature[] = [];
      const centroidByScenario = new Map<string, { x: number; y: number; n: number }>();
      const groups = new Map<string, { top: [number, number]; n: number; lls: number[] }>();
      for (const s of zone.sticks) {
        const scen = scenarioNames[s.scenario_ref];
        const short = scen && s.well_name.startsWith(`${scen} `) ? s.well_name.slice(scen.length + 1) : s.well_name;
        const ll = s.completed_lateral_ft;
        const g = groups.get(s.scenario_ref) ?? { top: [0, -90] as [number, number], n: 0, lls: [] };
        g.n += 1;
        if (ll) g.lls.push(ll);
        for (const [hx, hy, tx, ty] of s.legs_lonlat as Array<[number, number, number, number]>) {
          stickFeatures.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: [[hx, hy], [tx, ty]] },
            properties: {},
          });
          const theta =
            (Math.atan2(ty - hy, (tx - hx) * Math.cos((hy * Math.PI) / 180)) * 180) / Math.PI;
          const upright = theta > 90 ? theta - 180 : theta < -90 ? theta + 180 : theta;
          stickLabelFeatures.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: [(hx + tx) / 2, (hy + ty) / 2] },
            properties: {
              label: `${short}${ll ? ` · ${Math.round(ll).toLocaleString()} ft` : ""}`,
              rot: -upright,
            },
          });
          for (const [x, y] of [[hx, hy], [tx, ty]] as Array<[number, number]>) {
            if (y > g.top[1]) g.top = [x, y];
          }
          const acc = centroidByScenario.get(s.scenario_ref) ?? { x: 0, y: 0, n: 0 };
          acc.x += (hx + tx) / 2;
          acc.y += (hy + ty) / 2;
          acc.n += 1;
          centroidByScenario.set(s.scenario_ref, acc);
        }
        groups.set(s.scenario_ref, g);
      }
      const groupLabelFeatures: Feature[] = [...groups.entries()].map(([ref, g]) => {
        const lo = g.lls.length ? Math.round(Math.min(...g.lls)) : null;
        const hi = g.lls.length ? Math.round(Math.max(...g.lls)) : null;
        const range =
          lo === null || hi === null
            ? ""
            : lo === hi
              ? ` · ${Math.round(lo).toLocaleString()} ft`
              : ` · ${Math.round(lo).toLocaleString()}–${Math.round(hi).toLocaleString()} ft`;
        return {
          type: "Feature",
          geometry: { type: "Point", coordinates: g.top },
          properties: {
            label: `${scenarioNames[ref] ?? ref.split("/").pop()} — ${g.n} stick${g.n === 1 ? "" : "s"}${range}`,
          },
        };
      });

      // Cohort wells: lateral + midpoint (value label + link anchor).
      const wellFeatures: Feature[] = [];
      const midFeatures: Feature[] = [];
      const linkFeatures: Feature[] = [];
      const showValues = mode === "support" && zone.cohort.length <= MAX_VALUE_LABELS;
      for (const w of zone.cohort) {
        const props: Record<string, unknown> = {};
        if (w.oil_eur_per_ft !== null) props.eur_ft = w.oil_eur_per_ft;
        if (w.coords.length >= 2) {
          wellFeatures.push({
            type: "Feature",
            geometry: { type: "LineString", coordinates: w.coords },
            properties: props,
          });
        }
        const mid = midpoint(w.coords);
        if (!mid) continue;
        midFeatures.push({
          type: "Feature",
          geometry: { type: "Point", coordinates: mid },
          properties: {
            ...props,
            label: showValues
              ? w.oil_eur_per_ft !== null ? w.oil_eur_per_ft.toFixed(0) : "n/f"
              : "",
          },
        });
        if (mode === "support") {
          for (const c of centroidByScenario.values()) {
            linkFeatures.push({
              type: "Feature",
              geometry: { type: "LineString", coordinates: [[c.x / c.n, c.y / c.n], mid] },
              properties: {},
            });
          }
        }
      }

      const aoiFeatures: Feature[] = [];
      for (const a of aois) {
        try {
          aoiFeatures.push({ type: "Feature", geometry: JSON.parse(a) as GeoJSON.Geometry, properties: {} });
        } catch {
          // unparseable AOI: skip, the sticks still render
        }
      }

      const fc = (features: Feature[]) => ({ type: "FeatureCollection" as const, features });
      map.addSource(`${src}-aoi`, { type: "geojson", data: fc(aoiFeatures) });
      map.addSource(`${src}-links`, { type: "geojson", data: fc(linkFeatures) });
      map.addSource(`${src}-wells`, { type: "geojson", data: fc(wellFeatures) });
      map.addSource(`${src}-mids`, { type: "geojson", data: fc(midFeatures) });
      map.addSource(`${src}-sticks`, { type: "geojson", data: fc(stickFeatures) });
      map.addSource(`${src}-stick-pts`, { type: "geojson", data: fc(stickLabelFeatures) });
      map.addSource(`${src}-groups`, { type: "geojson", data: fc(groupLabelFeatures) });

      const layerIds: string[] = [];
      const add = (layer: maplibregl.LayerSpecification) => {
        map.addLayer(layer);
        layerIds.push(layer.id);
      };
      add({
        id: `${src}-aoi-fill`, type: "fill", source: `${src}-aoi`,
        paint: { "fill-color": color, "fill-opacity": 0.08 },
      });
      add({
        id: `${src}-aoi-line`, type: "line", source: `${src}-aoi`,
        paint: { "line-color": color, "line-width": 1.6, "line-opacity": 0.8 },
      });
      add({
        id: `${src}-links`, type: "line", source: `${src}-links`,
        paint: { "line-color": color, "line-width": 0.7, "line-opacity": 0.35 },
      });
      add({
        id: `${src}-wells-casing`, type: "line", source: `${src}-wells`,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#374151", "line-width": 5, "line-opacity": 0.6 },
      });
      add({
        id: `${src}-wells`, type: "line", source: `${src}-wells`,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": viridisExpression("eur_ft", eurRange.lo, eurRange.hi),
          "line-width": 3.4,
        },
      });
      // A well with no stick still shows: a dot at its midpoint.
      add({
        id: `${src}-mids-dot`, type: "circle", source: `${src}-mids`,
        paint: {
          "circle-color": viridisExpression("eur_ft", eurRange.lo, eurRange.hi),
          "circle-radius": 2.5,
          "circle-stroke-color": "#374151",
          "circle-stroke-width": 0.6,
        },
      });
      add({
        id: `${src}-sticks-casing`, type: "line", source: `${src}-sticks`,
        layout: { "line-cap": "round" },
        paint: { "line-color": "#ffffff", "line-width": 6 },
      });
      add({
        id: `${src}-sticks`, type: "line", source: `${src}-sticks`,
        layout: { "line-cap": "round" },
        paint: { "line-color": color, "line-width": 3.6 },
      });
      if (mode === "support") {
        add({
          id: `${src}-values`, type: "symbol", source: `${src}-mids`,
          layout: {
            "text-field": ["get", "label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 11,
            "text-offset": [0.9, 0],
            "text-anchor": "left",
            "text-allow-overlap": true,
          },
          paint: { "text-color": "#111827", "text-halo-color": "#ffffff", "text-halo-width": 1.6 },
        });
      } else {
        // Group labels first and always placed; stick labels collide
        // (shown only where they fit — zoom in to bring them all up).
        add({
          id: `${src}-group-labels`, type: "symbol", source: `${src}-groups`,
          layout: {
            "text-field": ["get", "label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 12,
            "text-max-width": 40,
            "text-anchor": "bottom",
            "text-offset": [0, -0.5],
            "text-allow-overlap": true,
            "text-ignore-placement": true,
          },
          paint: { "text-color": "#111827", "text-halo-color": "#ffffff", "text-halo-width": 2 },
        });
        add({
          id: `${src}-stick-labels`, type: "symbol", source: `${src}-stick-pts`,
          layout: {
            "text-field": ["get", "label"],
            "text-font": ["Noto Sans Regular"],
            "text-size": 10,
            "text-rotate": ["get", "rot"],
            "text-rotation-alignment": "map",
            "text-offset": [0, -0.7],
            "text-allow-overlap": false,
          },
          paint: { "text-color": color, "text-halo-color": "#ffffff", "text-halo-width": 2 },
        });
      }

      // Frame: support = sticks + cohort; sticks = the sticks (+ AOIs).
      const coords: Array<[number, number]> = [];
      for (const f of stickFeatures) {
        for (const c of (f.geometry as GeoJSON.LineString).coordinates) coords.push([c[0]!, c[1]!]);
      }
      if (mode === "support") {
        for (const f of wellFeatures) {
          for (const c of (f.geometry as GeoJSON.LineString).coordinates) coords.push([c[0]!, c[1]!]);
        }
      }
      if (!cam && coords.length > 0) {
        const b = new maplibregl.LngLatBounds(coords[0], coords[0]);
        for (const c of coords) b.extend(c);
        map.fitBounds(b, { padding: mode === "support" ? 36 : 70, duration: 0, maxZoom: 14 });
      }

      const legend =
        mode === "support"
          ? {
              rows: [
                { color, label: `planned stick taking ${zone.curve_name}`, kind: "line" as const },
                { color, label: "link: scenario sticks → curve well", kind: "thin" as const },
                { color: NO_FIT_COLOR, label: "curve well, no anduin fit", kind: "line" as const },
              ],
              colorBar: {
                lo: eurRange.lo,
                hi: eurRange.hi,
                label: "curve wells: anduin oil EUR, bbl/ft",
              },
            }
          : {
              rows: [
                { color, label: `${zone.n_sticks} sticks taking ${zone.curve_name}`, kind: "line" as const },
                { color: VIRIDIS[4]!, label: "curve wells in view (oil EUR/ft colour)", kind: "line" as const },
              ],
            };

      void Promise.allSettled([loadBlocks(map), loadSections(map)]).then(() => {
        for (const id of layerIds) if (map.getLayer(id)) map.moveLayer(id);
        const refresh = () => {
          try {
            setSnapshot(composeSnapshot(map.getCanvas(), width, legend));
          } catch (e) {
            console.error("zone support map snapshot failed", e);
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
    // Zone geometry + colour scale are load-time inputs; `live` alone
    // drives (lazy) mount/teardown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  return (
    <div ref={outerRef} className="slide-map" style={{ width, height, position: "relative" }}>
      <div ref={containerRef} className="slide-map-canvas" style={{ width, height }} />
      {snapshot && (
        <img
          src={snapshot}
          alt={mode === "support" ? "Curve support map" : "Sticks taking this curve"}
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
