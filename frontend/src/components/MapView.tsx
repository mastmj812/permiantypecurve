import { useEffect, useRef, useState } from "react";
import maplibregl, { type Map as MlMap, type StyleSpecification } from "maplibre-gl";
import { Protocol } from "pmtiles";
import layers from "protomaps-themes-base";
import "maplibre-gl/dist/maplibre-gl.css";

import { selectWellsSpatial, summaryForApi14s, tileUrlTemplate } from "../api/wells";
import { DrawingController } from "../map/drawing";
import {
  WELLS_INTERACTIVE_LAYERS,
  WELLS_SOURCE_ID,
  wellsLinesDashedLayer,
  wellsLinesSolidLayer,
  wellsPointsLayer,
} from "../map/wellsLayers";
import { useMapStore } from "../store/mapStore";

// Register the pmtiles:// protocol once per page. MapLibre's addProtocol is
// idempotent in practice but we guard anyway since StrictMode double-invokes.
let pmtilesRegistered = false;
function registerPmtilesProtocol() {
  if (pmtilesRegistered) return;
  const protocol = new Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  pmtilesRegistered = true;
}

const PERMIAN_CENTER: [number, number] = [-102.5, 32.0];
const PLSS_SOURCE_ID = "plss";
const PLSS_LAYER_ID = "plss-line";

function buildStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
    sources: {
      protomaps: {
        type: "vector",
        url: "pmtiles:///api/basemap/permian.pmtiles",
        attribution:
          '<a href="https://protomaps.com">Protomaps</a> © <a href="https://openstreetmap.org">OpenStreetMap</a>',
      },
    },
    layers: layers("protomaps", "light"),
  };
}

export function MapView() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const drawerRef = useRef<DrawingController | null>(null);
  const [tilesMissing, setTilesMissing] = useState(false);
  const [plssError, setPlssError] = useState<string | null>(null);
  // Style is loaded async; secondary effects that touch sources/layers
  // must wait for this to flip true (or MapLibre throws
  // "Style is not done loading").
  const [styleLoaded, setStyleLoaded] = useState(false);

  const filters = useMapStore((s) => s.filters);
  const drawMode = useMapStore((s) => s.drawMode);
  const showPlss = useMapStore((s) => s.showPlss);
  const showWellsticks = useMapStore((s) => s.showWellsticks);
  const selectedApi14s = useMapStore((s) => s.selectedApi14s);
  const setSelection = useMapStore((s) => s.setSelection);
  const toggleApi14 = useMapStore((s) => s.toggleApi14);

  // -------------- init map (once) --------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    registerPmtilesProtocol();

    fetch("/api/basemap/permian.pmtiles", { method: "HEAD" }).then((r) => {
      if (r.status === 404) setTilesMissing(true);
    });

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(),
      center: PERMIAN_CENTER,
      zoom: 6,
      minZoom: 3,
      maxZoom: 14,
      hash: true,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-left");

    // Idempotent setup: register wells source/layers + drawer. Called
    // from whichever map event fires first. If both fire, the second call
    // is a no-op (getSource check). The `load` event waits for the basemap
    // to finish loading — if pmtiles 404s, `load` may never fire — so we
    // also wire `styledata`, which fires as soon as the style JSON is
    // parsed and is independent of source-load status.
    const setupWellsAndDrawer = () => {
      if (map.getSource(WELLS_SOURCE_ID)) return;
      map.addSource(WELLS_SOURCE_ID, {
        type: "vector",
        tiles: [withOrigin(tileUrlTemplate(useMapStore.getState().filters))],
        minzoom: 3,
        maxzoom: 14,
        promoteId: { wells_points: "api14", wells_lines: "api14" },
      });
      map.addLayer(wellsPointsLayer);
      map.addLayer(wellsLinesSolidLayer);
      map.addLayer(wellsLinesDashedLayer);

      const drawer = new DrawingController(map, {
        onPolygon: async (polygon) => {
          try {
            const r = await selectWellsSpatial({
              polygon,
              filters: useMapStore.getState().filters,
            });
            setSelection(r.api14s, r.summary);
          } catch (e) {
            console.error("lasso selection failed", e);
          }
        },
        onBbox: async (bbox) => {
          try {
            const r = await selectWellsSpatial({
              bbox,
              filters: useMapStore.getState().filters,
            });
            setSelection(r.api14s, r.summary);
          } catch (e) {
            console.error("box selection failed", e);
          }
        },
        onClick: async (lngLat) => {
          const feats = map.queryRenderedFeatures(map.project(lngLat), {
            layers: WELLS_INTERACTIVE_LAYERS,
          });
          if (!feats.length) return;
          const api14 = feats[0]!.properties?.api14 as string | undefined;
          if (!api14) return;
          toggleApi14(api14);
          const nextSet = new Set(useMapStore.getState().selectedApi14s);
          const summary = await summaryForApi14s(Array.from(nextSet));
          setSelection(Array.from(nextSet), summary);
        },
      });
      drawer.install();
      drawerRef.current = drawer;
      setStyleLoaded(true);
    };

    map.on("load", setupWellsAndDrawer);
    map.on("styledata", setupWellsAndDrawer);

    mapRef.current = map;
    return () => {
      drawerRef.current?.uninstall();
      drawerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // -------------- filter changes → swap tile URL --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    const src = map.getSource(WELLS_SOURCE_ID);
    if (!src) return;
    // setTiles() swaps URL templates without dropping/re-adding the source,
    // which would otherwise force every layer to re-register.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (src as any).setTiles?.([withOrigin(tileUrlTemplate(filters))]);
  }, [filters, styleLoaded]);

  // -------------- draw mode --------------
  useEffect(() => {
    drawerRef.current?.setMode(drawMode);
  }, [drawMode]);

  // -------------- selection → featureState --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    map.removeFeatureState({ source: WELLS_SOURCE_ID, sourceLayer: "wells_points" });
    map.removeFeatureState({ source: WELLS_SOURCE_ID, sourceLayer: "wells_lines" });
    for (const api14 of selectedApi14s) {
      for (const sl of ["wells_points", "wells_lines"]) {
        map.setFeatureState(
          { source: WELLS_SOURCE_ID, sourceLayer: sl, id: api14 },
          { selected: true },
        );
      }
    }
  }, [selectedApi14s, styleLoaded]);

  // -------------- wellsticks toggle --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    const vis = showWellsticks ? "visible" : "none";
    for (const id of WELLS_INTERACTIVE_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
    }
  }, [showWellsticks, styleLoaded]);

  // -------------- PLSS overlay toggle --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (showPlss) {
      if (!map.getSource(PLSS_SOURCE_ID)) {
        fetch("/api/basemap/plss_tx_nm.geojson")
          .then((r) => {
            if (r.status === 404) {
              setPlssError("PLSS GeoJSON not loaded — see infra/basemap/README.md");
              useMapStore.getState().setShowPlss(false);
              return null;
            }
            if (!r.ok) throw new Error(`PLSS fetch ${r.status}`);
            return r.json();
          })
          .then((data) => {
            if (!data) return;
            setPlssError(null);
            map.addSource(PLSS_SOURCE_ID, { type: "geojson", data });
            map.addLayer({
              id: PLSS_LAYER_ID,
              type: "line",
              source: PLSS_SOURCE_ID,
              paint: { "line-color": "#475569", "line-width": 0.5, "line-opacity": 0.4 },
            });
          })
          .catch((e) => {
            console.error(e);
            setPlssError(String(e));
          });
      } else {
        map.setLayoutProperty(PLSS_LAYER_ID, "visibility", "visible");
      }
    } else if (map.getLayer(PLSS_LAYER_ID)) {
      map.setLayoutProperty(PLSS_LAYER_ID, "visibility", "none");
    }
  }, [showPlss, styleLoaded]);

  return (
    <div className="map-root">
      <div ref={containerRef} className="map-root" />
      {tilesMissing && (
        <div className="map-warning">
          <strong>Basemap not found.</strong> Run <code>infra/basemap/fetch.sh</code> (or{" "}
          <code>fetch.ps1</code> on Windows) to download the TX+NM PMTiles extract.
        </div>
      )}
      {plssError && (
        <div className="map-warning" style={{ top: 64 }}>
          <strong>PLSS overlay:</strong> {plssError}
        </div>
      )}
    </div>
  );
}

// MVT tiles are served same-origin (Vite proxies /api in dev; same-host in compose).
function withOrigin(template: string): string {
  return template.startsWith("/") ? `${window.location.origin}${template}` : template;
}
