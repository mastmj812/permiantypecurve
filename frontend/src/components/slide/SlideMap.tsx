// Slide-export map panel.
//
// Slimmer than the main MapView — no toolbar, no drawing, no popups,
// no cohort/selection state. Just the basemap + the cohort wells
// rendered in green, fit-bounded, then snapshotted to a PNG so the
// browser print step captures it reliably (live MapLibre canvases
// print as a black rectangle without `preserveDrawingBuffer`, and even
// then they sometimes blank out — the snapshot+swap trick sidesteps
// both pitfalls).

import { useEffect, useRef, useState } from "react";
import maplibregl, {
  type ExpressionSpecification,
  type LayerSpecification,
  type Map as MlMap,
  type RequestParameters,
  type StyleSpecification,
} from "maplibre-gl";
import { Protocol } from "pmtiles";
import layers from "protomaps-themes-base";
import "maplibre-gl/dist/maplibre-gl.css";

import { getStoredToken } from "../../api/auth";
import { ACREAGE_COLOR, fetchDealPolygonGeoJSON } from "../../api/dealPolygons";
import { DEFAULT_FILTER_SPEC } from "../../api/types";
import { type WellDetailLite, tileUrlTemplate } from "../../api/wells";
import { cohortLineFilter, WELLS_SOURCE_ID } from "../../map/wellsLayers";

// All cohort wells should render regardless of status (saved type
// curves can be built on a mix of PDP + DUC + SI), so override the
// default which scopes to PDP.
const SLIDE_FILTER_SPEC = {
  ...DEFAULT_FILTER_SPEC,
  statuses: [] as typeof DEFAULT_FILTER_SPEC.statuses,
};

interface Props {
  api10s: string[];
  wellDetails: WellDetailLite[];
  // Per-toggle overlay control. Default all true to preserve the
  // existing slide appearance; the parent (TypeCurveSlidePage)
  // wires checkboxes that flip these and remounts the component
  // via React key so the captured snapshot reflects the choice.
  // showDeals overlays ALL uploaded acreage polygons (no per-deal
  // filtering); the slide still fit-bounds to the wells, not the
  // acreage. The PowerPoint export's map PNG is captured AFTER the
  // polygon layer paints, so the outline goes along into the deck.
  showBlocks?: boolean;
  showSections?: boolean;
  showDeals?: boolean;
  width?: number;
  height?: number;
}

const COHORT_GREEN = "#16a34a";
const LAYER_SOLID = "slide-wells-lines-solid";

// Survey-grid overlays. Mirrors MapView's BLOCKS_* / SECTIONS_* config
// but always-on for the slide. The fetch URLs and min-zoom thresholds
// match the main map so the slide reads the same datasets the user
// already has seeded.
// No minzoom on slide overlays. The cohort fits to wildly different
// zoom levels depending on geographic spread, and the user wants the
// grid context visible at all of them. Labels would clutter at very
// low zoom in theory, but the slide is fit-bound at maxZoom=13 so in
// practice the clutter ceiling never hits.
const BLOCKS_SOURCE_ID = "slide-blocks";
const BLOCKS_FILL_LAYER = "slide-blocks-fill";
const BLOCKS_LINE_LAYER = "slide-blocks-line";
const BLOCKS_LABEL_LAYER = "slide-blocks-label";

const SECTIONS_SOURCE_ID = "slide-sections";
const SECTIONS_FILL_LAYER = "slide-sections-fill";
const SECTIONS_LINE_LAYER = "slide-sections-line";
const SECTIONS_LABEL_LAYER = "slide-sections-label";

const DEALS_SOURCE_ID = "slide-deal-polygons";
const DEALS_FILL_LAYER = "slide-deal-polygons-fill";
const DEALS_LINE_LAYER = "slide-deal-polygons-line";

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

async function loadBlocks(map: MlMap): Promise<void> {
  if (map.getSource(BLOCKS_SOURCE_ID)) return;
  const r = await fetch("/api/basemap/blocks_tx_nm.geojson");
  if (!r.ok) return;
  const data = await r.json();
  map.addSource(BLOCKS_SOURCE_ID, { type: "geojson", data });
  map.addLayer({
    id: BLOCKS_FILL_LAYER,
    type: "fill",
    source: BLOCKS_SOURCE_ID,
    paint: { "fill-color": "#000000", "fill-opacity": 0.03 },
  });
  map.addLayer({
    id: BLOCKS_LINE_LAYER,
    type: "line",
    source: BLOCKS_SOURCE_ID,
    paint: {
      // Dominant block boundary — pure black, thicker than the
      // section line, high opacity so it reads as the primary
      // grid on a projected slide.
      "line-color": "#000000",
      "line-width": 1.5,
      "line-opacity": 0.9,
    },
  });
  map.addLayer({
    id: BLOCKS_LABEL_LAYER,
    type: "symbol",
    source: BLOCKS_SOURCE_ID,
    layout: {
      "text-field": BLOCK_LABEL_EXPR,
      "text-size": 16,
      // protomaps' font CDN only ships Regular + Italic — requesting
      // "Noto Sans Bold" 404s on glyph fetch and silently kills the
      // entire block layer's render. We lean on the 2x size
      // differential vs sections (8pt) and a thicker halo for
      // emphasis instead.
      "text-font": ["Noto Sans Regular"],
      "text-allow-overlap": false,
      "symbol-placement": "point",
    },
    paint: {
      "text-color": "#000000",
      "text-halo-color": "rgba(255,255,255,1.0)",
      "text-halo-width": 2.5,
    },
  });
}

async function loadDealPolygons(map: MlMap): Promise<void> {
  if (map.getSource(DEALS_SOURCE_ID)) return;
  try {
    const fc = await fetchDealPolygonGeoJSON();
    if (fc.features.length === 0) return;
    map.addSource(DEALS_SOURCE_ID, {
      type: "geojson",
      data: fc as unknown as GeoJSON.FeatureCollection,
    });
    // Translucent fill + crisp outline so the wells and section grid
    // remain readable underneath. Single shared ACREAGE_COLOR — same
    // as the Map and Review tabs.
    map.addLayer({
      id: DEALS_FILL_LAYER,
      type: "fill",
      source: DEALS_SOURCE_ID,
      paint: {
        "fill-color": ACREAGE_COLOR,
        "fill-opacity": 0.14,
      },
    });
    map.addLayer({
      id: DEALS_LINE_LAYER,
      type: "line",
      source: DEALS_SOURCE_ID,
      paint: {
        "line-color": ACREAGE_COLOR,
        "line-width": 2.2,
        "line-opacity": 0.95,
      },
    });
  } catch (e) {
    console.warn("slide acreage polygons load failed", e);
  }
}


async function loadSections(map: MlMap): Promise<void> {
  if (map.getSource(SECTIONS_SOURCE_ID)) return;
  const r = await fetch("/api/basemap/sections_tx_nm.geojson");
  if (!r.ok) return;
  const data = await r.json();
  map.addSource(SECTIONS_SOURCE_ID, { type: "geojson", data });
  map.addLayer({
    id: SECTIONS_FILL_LAYER,
    type: "fill",
    source: SECTIONS_SOURCE_ID,
    paint: { "fill-color": "#9ca3af", "fill-opacity": 0.02 },
  });
  map.addLayer({
    id: SECTIONS_LINE_LAYER,
    type: "line",
    source: SECTIONS_SOURCE_ID,
    paint: {
      // Lighter / greyer than the block line so the block boundary
      // dominates visually; thinner too.
      "line-color": "#9ca3af",
      "line-width": 0.6,
      "line-opacity": 0.7,
    },
  });
  map.addLayer({
    id: SECTIONS_LABEL_LAYER,
    type: "symbol",
    source: SECTIONS_SOURCE_ID,
    layout: {
      "text-field": SECTION_LABEL_EXPR,
      "text-size": 8,
      "text-font": ["Noto Sans Regular"],
      "text-allow-overlap": false,
      "symbol-placement": "point",
    },
    paint: {
      "text-color": "#4b5563",
      "text-halo-color": "rgba(255,255,255,0.9)",
      "text-halo-width": 1.5,
    },
  });
}

let pmtilesRegistered = false;
function registerPmtilesProtocol() {
  if (pmtilesRegistered) return;
  const protocol = new Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  pmtilesRegistered = true;
}

function authedTransformRequest(url: string): RequestParameters | undefined {
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.origin === window.location.origin && parsed.pathname.startsWith("/api/")) {
      const token = getStoredToken();
      if (token) return { url, headers: { Authorization: `Bearer ${token}` } };
    }
  } catch {
    /* fall through */
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
      },
    },
    layers: layers("protomaps", "light", "en"),
  };
}

function withOrigin(template: string): string {
  return template.startsWith("/") ? `${window.location.origin}${template}` : template;
}

// Bounds tight around the cohort wells. Returns null when no well has
// a usable coordinate — the map then stays centered on its initial
// view rather than crashing fitBounds with a degenerate box.
function cohortBounds(details: WellDetailLite[]): maplibregl.LngLatBounds | null {
  // maplibregl.LngLatBounds() requires sw + ne; we don't know either
  // up front, so build from the first usable coord and extend from there.
  let b: maplibregl.LngLatBounds | null = null;
  for (const d of details) {
    for (const [lon, lat] of [
      [d.sh_lon, d.sh_lat],
      [d.bh_lon, d.bh_lat],
    ] as const) {
      if (lon != null && lat != null && Number.isFinite(lon) && Number.isFinite(lat)) {
        if (!b) b = new maplibregl.LngLatBounds([lon, lat], [lon, lat]);
        else b.extend([lon, lat]);
      }
    }
  }
  return b;
}

// Default ~6.93" × 4.34" at 96 px/in to match the export's
// slide-level map placement (right side, full chart-stack height).
export function SlideMap({
  api10s,
  wellDetails,
  showBlocks = true,
  showSections = true,
  showDeals = true,
  width = 665,
  height = 418,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [snapshot, setSnapshot] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    registerPmtilesProtocol();

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(),
      center: [-102.5, 32.0],
      zoom: 6,
      minZoom: 3,
      maxZoom: 14,
      attributionControl: false,
      transformRequest: authedTransformRequest,
      // Required for getCanvas().toDataURL() to return non-blank pixels.
      preserveDrawingBuffer: true,
    });

    const setup = () => {
      if (map.getSource(WELLS_SOURCE_ID)) return;
      // alwaysLines: the slide map asks the backend for wellstick
      // LINESTRINGs at every zoom (instead of the default points-below-
      // z9 / lines-above-z9 split). Lets us render wells as sticks
      // regardless of how spread out the cohort is — a wide deal can
      // fit-bound to z=6 where the default would serve dots.
      map.addSource(WELLS_SOURCE_ID, {
        type: "vector",
        tiles: [
          withOrigin(tileUrlTemplate(SLIDE_FILTER_SPEC, { alwaysLines: true })),
        ],
        minzoom: 3,
        maxzoom: 14,
        promoteId: { wells_lines: "api10" },
      });

      // Single solid line layer — the source serves line geometry at
      // every zoom (alwaysLines=true in the tile URL). Post-cutover
      // there's only one wellstick source (Novi 4-point), so no need
      // for the dashed "no survey confirmed" variant.
      const solidLayer: LayerSpecification = {
        id: LAYER_SOLID,
        type: "line",
        source: WELLS_SOURCE_ID,
        "source-layer": "wells_lines",
        filter: cohortLineFilter(api10s),
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": COHORT_GREEN,
          "line-width": [
            "interpolate", ["linear"], ["zoom"],
            5, 1.0,
            9, 2.0,
            12, 3.5,
            14, 5.0,
          ],
          "line-opacity": 0.95,
        },
      };
      map.addLayer(solidLayer);

      // Fit to the cohort — 12% padding so the wells aren't crammed
      // against the edge of the snapshot.
      const b = cohortBounds(wellDetails);
      if (b && !b.isEmpty()) {
        map.fitBounds(b, { padding: 40, duration: 0, maxZoom: 13 });
      }

      // Kick off the overlay fetches in parallel; allSettled so a
      // missing GeoJSON (404 on a fresh dev box) doesn't block the
      // snapshot. Snapshot fires on the FIRST idle after both fetches
      // resolve — that guarantees the blocks/sections paint into the
      // capture even though they arrive asynchronously well after the
      // basemap is otherwise idle.
      const overlayPromises: Array<Promise<unknown>> = [];
      if (showBlocks) overlayPromises.push(loadBlocks(map));
      if (showSections) overlayPromises.push(loadSections(map));
      if (showDeals) overlayPromises.push(loadDealPolygons(map));
      // Idle-listener fires even when no overlays loaded — the wells
      // still need to paint before we snapshot.
      if (overlayPromises.length === 0) overlayPromises.push(Promise.resolve());
      Promise.allSettled(overlayPromises).then(() => {
        // Enforce a deterministic z-order regardless of which fetch
        // resolved first. moveLayer(id) with no second arg moves the
        // layer to the TOP of the stack. We move sections-then-blocks
        // so blocks end up on top, then deal polygons on top of those
        // so the highlighted acreage outline reads above everything.
        const moveToTop = (id: string) => {
          if (map.getLayer(id)) map.moveLayer(id);
        };
        for (const id of [SECTIONS_FILL_LAYER, SECTIONS_LINE_LAYER, SECTIONS_LABEL_LAYER]) {
          moveToTop(id);
        }
        for (const id of [BLOCKS_FILL_LAYER, BLOCKS_LINE_LAYER, BLOCKS_LABEL_LAYER]) {
          moveToTop(id);
        }
        for (const id of [DEALS_FILL_LAYER, DEALS_LINE_LAYER]) {
          moveToTop(id);
        }
        map.once("idle", () => {
          try {
            const url = map.getCanvas().toDataURL("image/png");
            setSnapshot(url);
          } catch (e) {
            // CORS taint shouldn't happen here (same-origin tiles +
            // same-origin pmtiles + same-origin geojson), but log if
            // it does so the developer sees the cause of a blank map.
            console.error("slide map snapshot failed", e);
          }
        });
        // Nudge the renderer in case the map was already idle when the
        // overlay sources got added — without this, `once("idle")` can
        // sit waiting forever if the new sources don't trigger a redraw.
        map.triggerRepaint();
      });
    };

    map.on("load", setup);
    map.on("styledata", setup);

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // Effect runs once: cohort api10s + details are slide-time inputs,
    // not live state. Eslint disabled deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="slide-map" style={{ width, height }}>
      {/* Keep the map div mounted underneath; once we have a snapshot,
          paint the img on top. We can't unmount the map div because
          MapLibre relies on the container for its lifecycle. */}
      <div
        ref={containerRef}
        className="slide-map-canvas"
        style={{ width, height, visibility: snapshot ? "hidden" : "visible" }}
      />
      {snapshot && (
        <img
          src={snapshot}
          alt="Cohort well map"
          className="slide-map-img"
          style={{
            width,
            height,
            position: "absolute",
            top: 0,
            left: 0,
            objectFit: "contain",
          }}
        />
      )}
    </div>
  );
}
