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
  width?: number;
  height?: number;
}

const COHORT_GREEN = "#16a34a";
const LAYER_SOLID = "slide-wells-lines-solid";

// Survey-grid overlays. Mirrors MapView's BLOCKS_* / SECTIONS_* config
// but always-on for the slide. The fetch URLs and min-zoom thresholds
// match the main map so the slide reads the same datasets the user
// already has seeded.
const BLOCKS_SOURCE_ID = "slide-blocks";
const BLOCKS_FILL_LAYER = "slide-blocks-fill";
const BLOCKS_LINE_LAYER = "slide-blocks-line";
const BLOCKS_LABEL_LAYER = "slide-blocks-label";
// Lower than the main MapView (which uses 8 / 11) so blocks and
// sections always render on the slide map at typical cohort fit-
// bound zooms. A deal slide needs section context regardless of
// the cohort's geographic spread.
const BLOCKS_MIN_ZOOM = 6;

const SECTIONS_SOURCE_ID = "slide-sections";
const SECTIONS_FILL_LAYER = "slide-sections-fill";
const SECTIONS_LINE_LAYER = "slide-sections-line";
const SECTIONS_LABEL_LAYER = "slide-sections-label";
const SECTIONS_MIN_ZOOM = 9;

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
      // Larger than the main MapView's 12pt — slide is read at
      // print/projector scale, not interactive zoom.
      "text-size": 16,
      "text-font": ["Noto Sans Regular"],
      "text-allow-overlap": false,
      "symbol-placement": "point",
    },
    paint: {
      "text-color": "#0f172a",
      "text-halo-color": "rgba(255,255,255,0.95)",
      "text-halo-width": 2,
    },
  });
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
      // Bumped from 10pt to 12pt so section numbers read clearly
      // on a printed/projected deal slide.
      "text-size": 12,
      "text-font": ["Noto Sans Regular"],
      "text-allow-overlap": false,
      "symbol-placement": "point",
    },
    paint: {
      "text-color": "#1f2937",
      "text-halo-color": "rgba(255,255,255,0.95)",
      "text-halo-width": 1.75,
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
export function SlideMap({ api10s, wellDetails, width = 665, height = 418 }: Props) {
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
      Promise.allSettled([loadBlocks(map), loadSections(map)]).then(() => {
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
