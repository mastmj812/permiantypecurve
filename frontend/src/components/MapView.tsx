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
import { selectWellsSpatial, summaryForApi10s, tileUrlTemplate } from "../api/wells";
import { DrawingController } from "../map/drawing";
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
  const [tilesMissing, setTilesMissing] = useState(false);
  const [blocksError, setBlocksError] = useState<string | null>(null);
  const [sectionsError, setSectionsError] = useState<string | null>(null);
  // Style is loaded async; secondary effects that touch sources/layers
  // must wait for this to flip true (or MapLibre throws
  // "Style is not done loading").
  const [styleLoaded, setStyleLoaded] = useState(false);

  const filters = useMapStore((s) => s.filters);
  const drawMode = useMapStore((s) => s.drawMode);
  const showBlocks = useMapStore((s) => s.showBlocks);
  const showSections = useMapStore((s) => s.showSections);
  const showWellsticks = useMapStore((s) => s.showWellsticks);
  const dealPolygons = useMapStore((s) => s.dealPolygons);
  const showDealPolygons = useMapStore((s) => s.showDealPolygons);
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

      const drawer = new DrawingController(map, {
        onPolygon: async (polygon) => {
          try {
            const r = await selectWellsSpatial({
              polygon,
              filters: useMapStore.getState().filters,
            });
            setSelection(r.api10s, r.summary);
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
            setSelection(r.api10s, r.summary);
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
          const summary = await summaryForApi10s(Array.from(nextSet));
          setSelection(Array.from(nextSet), summary);
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
          .setHTML(buildWellPopupHtml(feat.properties as Record<string, unknown>))
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
            return r.json();
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
            return r.json();
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
    const existing = map.getSource(DEALS_SOURCE_ID) as
      | maplibregl.GeoJSONSource
      | undefined;
    if (existing) {
      existing.setData(dealPolygons as unknown as GeoJSON.FeatureCollection);
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

  // Single show/hide toggle for all uploaded acreage polygons.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map) return;
    const vis = showDealPolygons ? "visible" : "none";
    for (const id of [DEALS_FILL_LAYER, DEALS_LINE_LAYER]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
    }
  }, [showDealPolygons, dealPolygons, styleLoaded]);

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
  return String(s).replace(/[&<>"']/g, (c) => {
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
        <tr><td>Formation</td><td>${escHtml(p.formation)}</td></tr>
        <tr><td>Operator</td><td>${escHtml(p.operator)}</td></tr>
        <tr><td>Status</td><td>${escHtml(p.status)}</td></tr>
        <tr><td>Vintage</td><td>${escHtml(p.vintage_year)}</td></tr>
        <tr><td>Lateral</td><td>${fmtIntHtml(p.lateral_ft)} ft</td></tr>
      </table>
    </div>`;
}
