import { useEffect, useRef, useState } from "react";
import maplibregl, {
  type ExpressionSpecification,
  type MapGeoJSONFeature,
  type Map as MlMap,
  type MapMouseEvent,
  type RequestParameters,
  type StyleSpecification,
} from "maplibre-gl";
import { Protocol } from "pmtiles";
import layers from "protomaps-themes-base";
import "maplibre-gl/dist/maplibre-gl.css";

import { getStoredToken } from "../api/auth";
import { ACREAGE_COLOR, fetchDealPolygonGeoJSON } from "../api/dealPolygons";
import { fetchNarviDealSticks } from "../api/narvi";
import type { GeoJsonPolygon, LastDraw } from "../api/types";
import { selectWellsSpatial, summaryForApi10s, tileUrlTemplate } from "../api/wells";
import { dealVisibilityFilter } from "../map/dealVisibility";
import { DrawingController } from "../map/drawing";
import {
  NARVI_STICKS_LINE_LAYER,
  NARVI_STICKS_SOURCE_ID,
  benchKeysFor,
  buildNarviStickFeatures,
  narviBenchFilter,
} from "../map/narviSticks";
import { latestWins } from "../map/sequenced";
import {
  WELLS_INTERACTIVE_LAYERS,
  WELLS_LINES_SOLID_COHORT_LAYER,
  WELLS_POINTS_COHORT_LAYER,
  WELLS_SOURCE_ID,
  cohortLineFilter,
  cohortPointFilter,
  wellsLinesSolidCohortLayer,
  wellsLinesSolidLayer,
  wellsPointsCohortLayer,
  wellsPointsLayer,
} from "../map/wellsLayers";
import { useMapStore } from "../store/mapStore";
import { activeCohort, useCohortStore } from "../store/cohortStore";

const EMPTY_COHORT_API10S: string[] = [];

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

// GeoJSON overlays: Blocks + Sections (Texas/NM survey grid; OTLS data
// covers both states, so the older PLSS overlay was retired).
// (fill + line + symbol-label). The two abstract overlays render their
// number labels at the zooms below — the BLM cadastral grid is too dense
// to be useful before then.
const BLOCKS_SOURCE_ID = "blocks";
const BLOCKS_FILL_LAYER = "blocks-fill";
const BLOCKS_LINE_LAYER = "blocks-line";
const BLOCKS_LABEL_LAYER = "blocks-label";
const BLOCKS_MIN_ZOOM = 8;

const DEALS_SOURCE_ID = "deal-polygons";
const DEALS_FILL_LAYER = "deal-polygons-fill";
const DEALS_LINE_LAYER = "deal-polygons-line";

const SECTIONS_SOURCE_ID = "sections";
const SECTIONS_FILL_LAYER = "sections-fill";
const SECTIONS_LINE_LAYER = "sections-line";
const SECTIONS_LABEL_LAYER = "sections-label";
const SECTIONS_MIN_ZOOM = 11;

// Fault-trace overlays (BEG / Horne et al. line shapefiles, converted by
// infra/basemap/convert_shapefiles.{ps1,sh}). Line-only — no fill or
// label layers, and no minzoom gate: ~750 + ~420 traces render fine at
// basin scale, which is exactly where you want to see the structure.
const BASEMENT_FAULTS_SOURCE_ID = "basement-faults";
const BASEMENT_FAULTS_LINE_LAYER = "basement-faults-line";
const SNF_FAULTS_SOURCE_ID = "snf-faults";
const SNF_FAULTS_LINE_LAYER = "snf-faults-line";

// MapLibre text-field expression — try the most common BLM/abstract
// property names in order. Blank string at the end means "no label" if
// none of these keys are present, which keeps the layer from throwing.
const BLOCK_LABEL_EXPR = [
  "coalesce",
  ["get", "BLOCK_NO"],
  ["get", "BLOCK"],
  ["get", "BlockNo"],
  ["get", "Block"],
  ["get", "block"],
  ["get", "BLOCKID"],
  "",
] as unknown as ExpressionSpecification;

const SECTION_LABEL_EXPR = [
  "coalesce",
  // OTLS Texas GLO survey export: LEVEL3_SUR is the section number.
  ["get", "LEVEL3_SUR"],
  ["get", "SECTION_NO"],
  ["get", "SECTION"],
  ["get", "SEC"],
  ["get", "SectionNo"],
  ["get", "Section"],
  ["get", "section"],
  ["get", "SECTIONID"],
  "",
] as unknown as ExpressionSpecification;

// Same-origin /api/* requests are auth-gated. MapLibre's tile loader
// doesn't go through our apiFetch helper, so we attach the bearer here
// via MapLibre's transformRequest hook. Other URLs (basemap PMTiles
// served pre-auth, font CDN, etc.) pass through unchanged.
function authedTransformRequest(
  url: string,
): RequestParameters | undefined {
  try {
    const parsed = new URL(url, window.location.origin);
    if (
      parsed.origin === window.location.origin &&
      parsed.pathname.startsWith("/api/")
    ) {
      const token = getStoredToken();
      if (token) {
        return { url, headers: { Authorization: `Bearer ${token}` } };
      }
    }
  } catch {
    // Malformed URL — let MapLibre handle it untransformed.
  }
  return { url };
}

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
    layers: layers("protomaps", "light", "en"),
  };
}

export function MapView() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const drawerRef = useRef<DrawingController | null>(null);
  // One reusable popup for hover info — avoids flicker from creating /
  // destroying a Popup per mousemove. Cleaned up implicitly by map.remove().
  const popupRef = useRef<maplibregl.Popup | null>(null);
  // Monotonic sequence for click-selection summaries. Two quick clicks
  // overlap their summaryForApi10s requests; without this guard the
  // slower (older) response can resolve last and reset the selection
  // to the older set — a well visibly highlights then silently drops.
  const clickSeqRef = useRef(0);
  const [tilesMissing, setTilesMissing] = useState(false);
  const [blocksError, setBlocksError] = useState<string | null>(null);
  const [sectionsError, setSectionsError] = useState<string | null>(null);
  const [basementFaultsError, setBasementFaultsError] = useState<string | null>(null);
  const [snfFaultsError, setSnfFaultsError] = useState<string | null>(null);
  const [narviError, setNarviError] = useState<string | null>(null);
  // Style is loaded async; secondary effects that touch sources/layers
  // must wait for this to flip true (or MapLibre throws
  // "Style is not done loading").
  const [styleLoaded, setStyleLoaded] = useState(false);

  const filters = useMapStore((s) => s.filters);
  const drawMode = useMapStore((s) => s.drawMode);
  const showBlocks = useMapStore((s) => s.showBlocks);
  const showSections = useMapStore((s) => s.showSections);
  const showBasementFaults = useMapStore((s) => s.showBasementFaults);
  const showSnfFaults = useMapStore((s) => s.showSnfFaults);
  const showWellsticks = useMapStore((s) => s.showWellsticks);
  const dealPolygons = useMapStore((s) => s.dealPolygons);
  const dealVisibility = useMapStore((s) => s.dealVisibility);
  const showNarviSticks = useMapStore((s) => s.showNarviSticks);
  const narviDealIds = useMapStore((s) => s.narviDealIds);
  const narviSticks = useMapStore((s) => s.narviSticks);
  const narviBenchVisibility = useMapStore((s) => s.narviBenchVisibility);
  const selectedApi10s = useMapStore((s) => s.selectedApi10s);
  const setSelection = useMapStore((s) => s.setSelection);
  const toggleApi10 = useMapStore((s) => s.toggleApi10);
  // Cohort api10s drive the sky-blue halo. Stable empty-array fallback
  // so React doesn't re-run the effect just because there's no cohort.
  const cohortApi10s = useCohortStore(
    (s) => activeCohort(s)?.api10s ?? EMPTY_COHORT_API10S,
  );

  // -------------- init map (once) --------------
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    registerPmtilesProtocol();

    void fetch("/api/basemap/permian.pmtiles", { method: "HEAD" }).then((r) => {
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
      transformRequest: authedTransformRequest,
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
        promoteId: { wells_points: "api10", wells_lines: "api10" },
      });
      // Cohort halos are added BEFORE the matching regular layer so
      // they paint underneath — the formation-colored stroke/circle
      // ends up centered inside the sky-blue halo for cohort wells.
      map.addLayer(wellsPointsCohortLayer);
      map.addLayer(wellsPointsLayer);
      map.addLayer(wellsLinesSolidCohortLayer);
      map.addLayer(wellsLinesSolidLayer);

      // Lasso and box selections share one selection channel, so a
      // single latest-wins runner guards both: if the user draws again
      // before the previous spatial request resolves, the older (slower)
      // response is dropped instead of clobbering the fresh selection.
      // (onClick has its own equivalent guard via clickSeqRef.)
      // The winning result also records the draw (polygon + filters +
      // returned api10s) as mapStore.lastDraw, so "Add staged" can
      // attribute the staged wells to this AOI in the build-up
      // provenance. Stale (dropped) responses never touch lastDraw.
      const runSpatialSelect = latestWins<{
        r: Awaited<ReturnType<typeof selectWellsSpatial>>;
        draw: Omit<LastDraw, "api10s">;
      }>(({ r, draw }) => {
        setSelection(r.api10s, r.summary);
        useMapStore.getState().setLastDraw({ ...draw, api10s: r.api10s });
      });

      const drawer = new DrawingController(map, {
        onPolygon: async (polygon) => {
          const filters = useMapStore.getState().filters;
          const at = new Date().toISOString();
          try {
            await runSpatialSelect(() =>
              selectWellsSpatial({ polygon, filters }).then((r) => ({
                r,
                draw: { kind: "polygon" as const, polygon, filters, at },
              })),
            );
          } catch (e) {
            console.error("lasso selection failed", e);
          }
        },
        onBbox: async (bbox) => {
          const filters = useMapStore.getState().filters;
          const at = new Date().toISOString();
          // Provenance speaks Polygon only — convert the box to a
          // 5-point ring for the record; the server still gets the
          // bbox (same selection path as before).
          const [w, s, e, n] = bbox;
          const boxPolygon: GeoJsonPolygon = {
            type: "Polygon",
            coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
          };
          try {
            await runSpatialSelect(() =>
              selectWellsSpatial({ bbox, filters }).then((r) => ({
                r,
                draw: {
                  kind: "bbox" as const,
                  polygon: boxPolygon,
                  filters,
                  at,
                },
              })),
            );
          } catch (e) {
            console.error("box selection failed", e);
          }
        },
        onClick: async (lngLat) => {
          const feats = map.queryRenderedFeatures(map.project(lngLat), {
            layers: WELLS_INTERACTIVE_LAYERS,
          });
          if (!feats.length) return;
          const api10 = feats[0]!.properties?.api10 as string | undefined;
          if (!api10) return;
          toggleApi10(api10);
          const nextSet = new Set(useMapStore.getState().selectedApi10s);
          const seq = ++clickSeqRef.current;
          try {
            const summary = await summaryForApi10s(Array.from(nextSet));
            // A newer click superseded this request — drop the stale
            // result instead of resetting the selection to nextSet.
            if (seq !== clickSeqRef.current) return;
            setSelection(Array.from(nextSet), summary);
          } catch (e) {
            // toggleApi10 already updated the store, so the highlight
            // stays consistent with what the user clicked — only the
            // drawer summary stats may be stale.
            console.error("click selection summary failed", e);
          }
        },
      });
      drawer.install();
      drawerRef.current = drawer;

      // Hover tooltip across all wells layers (points at low zoom, lines
      // at high zoom). The popup is pointer-events: none in CSS, so the
      // click handler the DrawingController installs still receives the
      // event when the user clicks a well underneath the popup.
      popupRef.current = new maplibregl.Popup({
        closeButton: false,
        closeOnClick: false,
        offset: 10,
        className: "map-tooltip",
      });
      // Layer-scoped mousemove fires with the queried features attached;
      // MapLibre v4 doesn't export the narrowed event type publicly, so
      // we widen MapMouseEvent with the features field locally.
      const onMove = (e: MapMouseEvent & { features?: MapGeoJSONFeature[] }) => {
        const feat = e.features?.[0];
        if (!feat) return;
        map.getCanvas().style.cursor = "pointer";
        popupRef.current!
          .setLngLat(e.lngLat)
          .setHTML(buildWellPopupHtml(feat.properties))
          .addTo(map);
      };
      const onLeave = () => {
        map.getCanvas().style.cursor = "";
        popupRef.current?.remove();
      };
      for (const id of WELLS_INTERACTIVE_LAYERS) {
        map.on("mousemove", id, onMove);
        map.on("mouseleave", id, onLeave);
      }

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
    const src = map.getSource<maplibregl.VectorTileSource>(WELLS_SOURCE_ID);
    if (!src) return;
    // setTiles() swaps URL templates without dropping/re-adding the source,
    // which would otherwise force every layer to re-register.
    src.setTiles([withOrigin(tileUrlTemplate(filters))]);
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
    for (const api10 of selectedApi10s) {
      for (const sl of ["wells_points", "wells_lines"]) {
        map.setFeatureState(
          { source: WELLS_SOURCE_ID, sourceLayer: sl, id: api10 },
          { selected: true },
        );
      }
    }
  }, [selectedApi10s, styleLoaded]);

  // -------------- active cohort → halo layer filters --------------
  // Drives the sky-blue halo by swapping each cohort halo layer's
  // filter to match only api10s in the active cohort. Filter-based
  // (vs feature-state) so membership doesn't depend on tiles being
  // loaded at write time — MapLibre re-evaluates the filter every
  // time tiles render.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (map.getLayer(WELLS_POINTS_COHORT_LAYER)) {
      map.setFilter(WELLS_POINTS_COHORT_LAYER, cohortPointFilter(cohortApi10s));
    }
    if (map.getLayer(WELLS_LINES_SOLID_COHORT_LAYER)) {
      map.setFilter(
        WELLS_LINES_SOLID_COHORT_LAYER,
        cohortLineFilter(cohortApi10s),
      );
    }
  }, [cohortApi10s, styleLoaded]);

  // -------------- wellsticks toggle --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    const vis = showWellsticks ? "visible" : "none";
    for (const id of WELLS_INTERACTIVE_LAYERS) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
    }
    // Halo layers track the same toggle so cohort wells don't keep
    // glowing after the user hides the underlying wellsticks.
    for (const id of [
      WELLS_POINTS_COHORT_LAYER,
      WELLS_LINES_SOLID_COHORT_LAYER,
    ]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
    }
  }, [showWellsticks, styleLoaded]);

  // -------------- Blocks overlay toggle --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (showBlocks) {
      if (!map.getSource(BLOCKS_SOURCE_ID)) {
        fetch("/api/basemap/blocks_tx_nm.geojson")
          .then((r) => {
            if (r.status === 404) {
              setBlocksError(
                "Blocks GeoJSON not loaded — run infra/basemap/convert_shapefiles.{ps1,sh}.",
              );
              useMapStore.getState().setShowBlocks(false);
              return null;
            }
            if (!r.ok) throw new Error(`Blocks fetch ${r.status}`);
            return r.json() as Promise<GeoJSON.GeoJSON>;
          })
          .then((data) => {
            if (!data) return;
            setBlocksError(null);
            map.addSource(BLOCKS_SOURCE_ID, { type: "geojson", data });
            // Fill is barely there — keeps the polygon footprint visible
            // without competing with wellsticks for attention.
            map.addLayer({
              id: BLOCKS_FILL_LAYER,
              type: "fill",
              source: BLOCKS_SOURCE_ID,
              minzoom: BLOCKS_MIN_ZOOM,
              paint: { "fill-color": "#1e293b", "fill-opacity": 0.04 },
            });
            map.addLayer({
              id: BLOCKS_LINE_LAYER,
              type: "line",
              source: BLOCKS_SOURCE_ID,
              minzoom: BLOCKS_MIN_ZOOM,
              paint: {
                "line-color": "#1e293b",
                "line-width": 0.9,
                "line-opacity": 0.55,
              },
            });
            map.addLayer({
              id: BLOCKS_LABEL_LAYER,
              type: "symbol",
              source: BLOCKS_SOURCE_ID,
              minzoom: BLOCKS_MIN_ZOOM,
              layout: {
                "text-field": BLOCK_LABEL_EXPR,
                "text-size": 12,
                "text-font": ["Noto Sans Regular"],
                "text-allow-overlap": false,
                "symbol-placement": "point",
              },
              paint: {
                "text-color": "#0f172a",
                "text-halo-color": "rgba(255,255,255,0.9)",
                "text-halo-width": 1.5,
              },
            });
          })
          .catch((e) => {
            console.error(e);
            setBlocksError(String(e));
          });
      } else {
        for (const id of [BLOCKS_FILL_LAYER, BLOCKS_LINE_LAYER, BLOCKS_LABEL_LAYER]) {
          if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
        }
      }
    } else {
      for (const id of [BLOCKS_FILL_LAYER, BLOCKS_LINE_LAYER, BLOCKS_LABEL_LAYER]) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
      }
    }
  }, [showBlocks, styleLoaded]);

  // -------------- Sections overlay toggle --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (showSections) {
      if (!map.getSource(SECTIONS_SOURCE_ID)) {
        fetch("/api/basemap/sections_tx_nm.geojson")
          .then((r) => {
            if (r.status === 404) {
              setSectionsError(
                "Sections GeoJSON not loaded — run infra/basemap/convert_shapefiles.{ps1,sh}.",
              );
              useMapStore.getState().setShowSections(false);
              return null;
            }
            if (!r.ok) throw new Error(`Sections fetch ${r.status}`);
            return r.json() as Promise<GeoJSON.GeoJSON>;
          })
          .then((data) => {
            if (!data) return;
            setSectionsError(null);
            map.addSource(SECTIONS_SOURCE_ID, { type: "geojson", data });
            map.addLayer({
              id: SECTIONS_FILL_LAYER,
              type: "fill",
              source: SECTIONS_SOURCE_ID,
              minzoom: SECTIONS_MIN_ZOOM,
              paint: { "fill-color": "#475569", "fill-opacity": 0.03 },
            });
            map.addLayer({
              id: SECTIONS_LINE_LAYER,
              type: "line",
              source: SECTIONS_SOURCE_ID,
              minzoom: SECTIONS_MIN_ZOOM,
              paint: {
                "line-color": "#475569",
                "line-width": 0.6,
                "line-opacity": 0.5,
              },
            });
            map.addLayer({
              id: SECTIONS_LABEL_LAYER,
              type: "symbol",
              source: SECTIONS_SOURCE_ID,
              minzoom: SECTIONS_MIN_ZOOM,
              layout: {
                "text-field": SECTION_LABEL_EXPR,
                "text-size": 10,
                "text-font": ["Noto Sans Regular"],
                "text-allow-overlap": false,
                "symbol-placement": "point",
              },
              paint: {
                "text-color": "#334155",
                "text-halo-color": "rgba(255,255,255,0.9)",
                "text-halo-width": 1.25,
              },
            });
          })
          .catch((e) => {
            console.error(e);
            setSectionsError(String(e));
          });
      } else {
        for (const id of [SECTIONS_FILL_LAYER, SECTIONS_LINE_LAYER, SECTIONS_LABEL_LAYER]) {
          if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
        }
      }
    } else {
      for (const id of [SECTIONS_FILL_LAYER, SECTIONS_LINE_LAYER, SECTIONS_LABEL_LAYER]) {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
      }
    }
  }, [showSections, styleLoaded]);

  // -------------- Basement-fault overlay toggle --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (showBasementFaults) {
      if (!map.getSource(BASEMENT_FAULTS_SOURCE_ID)) {
        fetch("/api/basemap/faults_basement.geojson")
          .then((r) => {
            if (r.status === 404) {
              setBasementFaultsError(
                "Basement-faults GeoJSON not loaded — run infra/basemap/convert_shapefiles.{ps1,sh}.",
              );
              useMapStore.getState().setShowBasementFaults(false);
              return null;
            }
            if (!r.ok) throw new Error(`Basement faults fetch ${r.status}`);
            return r.json() as Promise<GeoJSON.GeoJSON>;
          })
          .then((data) => {
            if (!data) return;
            setBasementFaultsError(null);
            map.addSource(BASEMENT_FAULTS_SOURCE_ID, { type: "geojson", data });
            map.addLayer({
              id: BASEMENT_FAULTS_LINE_LAYER,
              type: "line",
              source: BASEMENT_FAULTS_SOURCE_ID,
              paint: {
                "line-color": "#b91c1c",
                "line-width": 1.4,
                "line-opacity": 0.8,
              },
            });
          })
          .catch((e) => {
            console.error(e);
            setBasementFaultsError(String(e));
          });
      } else if (map.getLayer(BASEMENT_FAULTS_LINE_LAYER)) {
        map.setLayoutProperty(BASEMENT_FAULTS_LINE_LAYER, "visibility", "visible");
      }
    } else if (map.getLayer(BASEMENT_FAULTS_LINE_LAYER)) {
      map.setLayoutProperty(BASEMENT_FAULTS_LINE_LAYER, "visibility", "none");
    }
  }, [showBasementFaults, styleLoaded]);

  // -------------- Shallow-normal-fault (SNF) overlay toggle --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (showSnfFaults) {
      if (!map.getSource(SNF_FAULTS_SOURCE_ID)) {
        fetch("/api/basemap/faults_snf.geojson")
          .then((r) => {
            if (r.status === 404) {
              setSnfFaultsError(
                "SNF GeoJSON not loaded — run infra/basemap/convert_shapefiles.{ps1,sh}.",
              );
              useMapStore.getState().setShowSnfFaults(false);
              return null;
            }
            if (!r.ok) throw new Error(`SNF fetch ${r.status}`);
            return r.json() as Promise<GeoJSON.GeoJSON>;
          })
          .then((data) => {
            if (!data) return;
            setSnfFaultsError(null);
            map.addSource(SNF_FAULTS_SOURCE_ID, { type: "geojson", data });
            // Dashed to distinguish from the solid basement traces where
            // the two sets overlap (Waha–Lockridge, Culberson-Reeves).
            map.addLayer({
              id: SNF_FAULTS_LINE_LAYER,
              type: "line",
              source: SNF_FAULTS_SOURCE_ID,
              paint: {
                "line-color": "#d97706",
                "line-width": 1.4,
                "line-opacity": 0.9,
                "line-dasharray": [2, 2],
              },
            });
          })
          .catch((e) => {
            console.error(e);
            setSnfFaultsError(String(e));
          });
      } else if (map.getLayer(SNF_FAULTS_LINE_LAYER)) {
        map.setLayoutProperty(SNF_FAULTS_LINE_LAYER, "visibility", "visible");
      }
    } else if (map.getLayer(SNF_FAULTS_LINE_LAYER)) {
      map.setLayoutProperty(SNF_FAULTS_LINE_LAYER, "visibility", "none");
    }
  }, [showSnfFaults, styleLoaded]);

  // -------------- Deal-acreage polygons --------------
  // One-shot fetch into the store if not already loaded. The admin
  // modal's refresh-after-mutation flow goes through the store too,
  // so MapView just observes.
  useEffect(() => {
    if (dealPolygons !== null) return;
    let cancelled = false;
    fetchDealPolygonGeoJSON()
      .then((fc) => {
        if (cancelled) return;
        useMapStore.getState().setDealPolygons(fc);
      })
      .catch((e) => {
        console.warn("acreage polygons fetch failed", e);
      });
    return () => {
      cancelled = true;
    };
  }, [dealPolygons]);

  // Source + layers. Update the source's data on every dealPolygons
  // change so a post-upload refetch re-renders without remounting the
  // map.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (!dealPolygons) return;
    const existing = map.getSource<maplibregl.GeoJSONSource>(DEALS_SOURCE_ID);
    if (existing) {
      existing.setData(dealPolygons);
    } else {
      map.addSource(DEALS_SOURCE_ID, {
        type: "geojson",
        data: dealPolygons as unknown as GeoJSON.FeatureCollection,
      });
      map.addLayer({
        id: DEALS_FILL_LAYER,
        type: "fill",
        source: DEALS_SOURCE_ID,
        paint: {
          "fill-color": ACREAGE_COLOR,
          "fill-opacity": 0.15,
        },
      });
      map.addLayer({
        id: DEALS_LINE_LAYER,
        type: "line",
        source: DEALS_SOURCE_ID,
        paint: {
          "line-color": ACREAGE_COLOR,
          "line-width": 1.6,
          "line-opacity": 0.95,
        },
      });
    }
  }, [dealPolygons, styleLoaded]);

  // Per-shapefile visibility — hide features whose source_file is
  // toggled off in the manage modal. Missing/true = visible. Legacy
  // rows with a null source_file fall into the NO_SOURCE_FILE bucket
  // via coalesce so they can be toggled too.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (!map.getLayer(DEALS_FILL_LAYER)) return;
    const filter = dealVisibilityFilter(dealVisibility);
    map.setFilter(DEALS_FILL_LAYER, filter);
    map.setFilter(DEALS_LINE_LAYER, filter);
  }, [dealVisibility, dealPolygons, styleLoaded]);

  // -------------- Narvi planned-stick overlay --------------
  // Fetch once per deal selection when the overlay is enabled (the
  // store nulls the payload on any selection change, so a stale set is
  // never re-shown). On failure surface the banner and flip the toggle
  // back off (Blocks 404 pattern) so the checkbox never lies about
  // what's on screen.
  useEffect(() => {
    if (!showNarviSticks || narviDealIds.length === 0) return;
    if (useMapStore.getState().narviSticks !== null) return;
    let cancelled = false;
    fetchNarviDealSticks(narviDealIds)
      .then((sticks) => {
        if (cancelled) return;
        setNarviError(null);
        useMapStore.getState().setNarviSticks(sticks);
      })
      .catch((e) => {
        if (cancelled) return;
        console.error("narvi deal sticks fetch failed", e);
        setNarviError(String(e));
        useMapStore.getState().setShowNarviSticks(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showNarviSticks, narviDealIds]);

  // Source + dashed line layer. setData on every payload change so a
  // deal switch re-renders without remounting the map. Added lazily on
  // first load, so the layer naturally paints above the solid PDP
  // wellsticks. Re-apply filter + visibility after a (re-)add so the
  // layer always lands consistent with the store.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (!narviSticks) return;
    const fc = buildNarviStickFeatures(narviSticks);
    const existing = map.getSource<maplibregl.GeoJSONSource>(NARVI_STICKS_SOURCE_ID);
    if (existing) {
      existing.setData(fc);
    } else {
      map.addSource(NARVI_STICKS_SOURCE_ID, { type: "geojson", data: fc });
      map.addLayer({
        id: NARVI_STICKS_LINE_LAYER,
        type: "line",
        source: NARVI_STICKS_SOURCE_ID,
        // Default butt caps keep the [2,2] dash legible — round caps
        // visually fuse the gaps.
        paint: {
          "line-color": ["get", "color"] as unknown as ExpressionSpecification,
          "line-dasharray": [2, 2],
          // ["zoom"] interpolate must stay the OUTERMOST expression.
          "line-width": [
            "interpolate", ["linear"], ["zoom"],
            8, 1.2,
            11, 2.6,
            14, 4.5,
          ] as unknown as ExpressionSpecification,
          "line-opacity": 0.95,
        },
      });
    }
    const state = useMapStore.getState();
    map.setFilter(
      NARVI_STICKS_LINE_LAYER,
      narviBenchFilter(benchKeysFor(narviSticks), state.narviBenchVisibility),
    );
    map.setLayoutProperty(
      NARVI_STICKS_LINE_LAYER,
      "visibility",
      state.showNarviSticks ? "visible" : "none",
    );
  }, [narviSticks, styleLoaded]);

  // Master toggle — pure visibility flip, no refetch.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (!map.getLayer(NARVI_STICKS_LINE_LAYER)) return;
    map.setLayoutProperty(
      NARVI_STICKS_LINE_LAYER,
      "visibility",
      showNarviSticks ? "visible" : "none",
    );
  }, [showNarviSticks, styleLoaded]);

  // Per-bench filter — sticks only; the PDP wells layers are untouched.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    if (!narviSticks) return;
    if (!map.getLayer(NARVI_STICKS_LINE_LAYER)) return;
    map.setFilter(
      NARVI_STICKS_LINE_LAYER,
      narviBenchFilter(benchKeysFor(narviSticks), narviBenchVisibility),
    );
  }, [narviBenchVisibility, narviSticks, styleLoaded]);

  return (
    <div className="map-root">
      <div ref={containerRef} className="map-root" />
      {tilesMissing && (
        <div className="map-warning">
          <strong>Basemap not found.</strong> Run <code>infra/basemap/fetch.sh</code> (or{" "}
          <code>fetch.ps1</code> on Windows) to download the TX+NM PMTiles extract.
        </div>
      )}
      {blocksError && (
        <div className="map-warning" style={{ top: 64 }}>
          <strong>Blocks overlay:</strong> {blocksError}
        </div>
      )}
      {sectionsError && (
        <div className="map-warning" style={{ top: 168 }}>
          <strong>Sections overlay:</strong> {sectionsError}
        </div>
      )}
      {basementFaultsError && (
        <div className="map-warning" style={{ top: 272 }}>
          <strong>Basement-faults overlay:</strong> {basementFaultsError}
        </div>
      )}
      {snfFaultsError && (
        <div className="map-warning" style={{ top: 376 }}>
          <strong>SNF overlay:</strong> {snfFaultsError}
        </div>
      )}
      {narviError && (
        <div className="map-warning" style={{ top: 480 }}>
          <strong>Narvi sticks overlay:</strong> {narviError}
        </div>
      )}
    </div>
  );
}

// MVT tiles are served same-origin (Vite proxies /api in dev; same-host in compose).
function withOrigin(template: string): string {
  return template.startsWith("/") ? `${window.location.origin}${template}` : template;
}

// Escape user-controlled strings before injecting into setHTML — operator
// names and formation labels come from the upstream warehouse and could
// in principle contain HTML metacharacters.
function escHtml(s: unknown): string {
  if (s == null) return "—";
  const str =
    typeof s === "string" ||
    typeof s === "number" ||
    typeof s === "boolean" ||
    typeof s === "bigint" ||
    typeof s === "symbol"
      ? String(s)
      : JSON.stringify(s);
  return str.replace(/[&<>"']/g, (c) => {
    switch (c) {
      case "&": return "&amp;";
      case "<": return "&lt;";
      case ">": return "&gt;";
      case '"': return "&quot;";
      case "'": return "&#39;";
      default:  return c;
    }
  });
}

function fmtIntHtml(v: unknown): string {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  return Math.round(Number(v)).toLocaleString();
}

function buildWellPopupHtml(p: Record<string, unknown>): string {
  // Headline is the human-readable name; API10 demotes into the table.
  // Falls back to API10 in the headline when the well's name is missing
  // so the popup still identifies the well.
  const name = (p.name as string | null | undefined) ?? null;
  const headline = name ? escHtml(name) : `API10 ${escHtml(p.api10)}`;
  const api10Row = name
    ? `<tr><td>API10</td><td>${escHtml(p.api10)}</td></tr>`
    : "";
  return `
    <div class="mtt">
      <div class="mtt-name">${headline}</div>
      <table class="mtt-table">
        ${api10Row}
        <tr><td>Formation</td><td>${escHtml(p.formation_blueox)}${
          p.formation ? ` <span class="muted">(${escHtml(p.formation)})</span>` : ""
        }</td></tr>
        <tr><td>Operator</td><td>${escHtml(p.operator)}</td></tr>
        <tr><td>Status</td><td>${escHtml(p.status)}</td></tr>
        <tr><td>Vintage</td><td>${escHtml(p.vintage_year)}</td></tr>
        <tr><td>Lateral</td><td>${fmtIntHtml(p.lateral_ft)} ft</td></tr>
      </table>
    </div>`;
}
