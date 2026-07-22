// Shared MapLibre infrastructure for the export-preview maps
// (SlideMap and the dossier's ScenarioSlideMap): pmtiles protocol,
// authed tile requests, the basemap style, and the blocks/sections
// survey-grid overlays. Split from SlideMap.tsx so component files
// only export components (react-refresh constraint).

import maplibregl, {
  type ExpressionSpecification,
  type Map as MlMap,
  type RequestParameters,
  type StyleSpecification,
} from "maplibre-gl";
import { Protocol } from "pmtiles";
import layers from "protomaps-themes-base";

import { getStoredToken } from "../../api/auth";

// Survey-grid overlays. Mirrors MapView's BLOCKS_* / SECTIONS_* config
// but always-on for exports. The fetch URLs and zoom behavior match
// the main map so exports read the same datasets the user has seeded.
// No minzoom on the lines: exports fit-bound to wildly different zooms
// and the grid context should be visible at all of them.
export const BLOCKS_SOURCE_ID = "slide-blocks";
export const BLOCKS_FILL_LAYER = "slide-blocks-fill";
export const BLOCKS_LINE_LAYER = "slide-blocks-line";
export const BLOCKS_LABEL_LAYER = "slide-blocks-label";

export const SECTIONS_SOURCE_ID = "slide-sections";
export const SECTIONS_FILL_LAYER = "slide-sections-fill";
export const SECTIONS_LINE_LAYER = "slide-sections-line";
export const SECTIONS_LABEL_LAYER = "slide-sections-label";

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

export async function loadBlocks(map: MlMap): Promise<void> {
  if (map.getSource(BLOCKS_SOURCE_ID)) return;
  const r = await fetch("/api/basemap/blocks_tx_nm.geojson");
  if (!r.ok) return;
  const data = (await r.json()) as GeoJSON.GeoJSON;
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
      // Zoom-scaled: a wide cohort fit-bounds to z~9-10 where the old
      // hard-coded 16px read enormous on the slide. ["zoom"] must be
      // the OUTERMOST expression (nesting it inside case/match
      // silently kills the layer — see project MapLibre gotchas).
      "text-size": [
        "interpolate", ["linear"], ["zoom"],
        7, 8,
        9, 10,
        11, 13,
        13, 16,
      ],
      // protomaps' font CDN only ships Regular + Italic — requesting
      // "Noto Sans Bold" 404s on glyph fetch and silently kills the
      // entire block layer's render. We lean on the size differential
      // vs sections and a thicker halo for emphasis instead.
      "text-font": ["Noto Sans Regular"],
      "text-allow-overlap": false,
      "symbol-placement": "point",
    },
    paint: {
      "text-color": "#000000",
      "text-halo-color": "rgba(255,255,255,1.0)",
      "text-halo-width": 2.0,
    },
  });
}

export async function loadSections(map: MlMap): Promise<void> {
  if (map.getSource(SECTIONS_SOURCE_ID)) return;
  const r = await fetch("/api/basemap/sections_tx_nm.geojson");
  if (!r.ok) return;
  const data = (await r.json()) as GeoJSON.GeoJSON;
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
    // Section labels are unreadable confetti below ~z11 (a 1-sq-mi
    // grid at county extent). The lines stay at every zoom for grid
    // context; the numbers only appear once the user zooms in far
    // enough for them to resolve.
    minzoom: 11,
    layout: {
      "text-field": SECTION_LABEL_EXPR,
      "text-size": [
        "interpolate", ["linear"], ["zoom"],
        11, 7,
        13, 9,
      ],
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
export function registerPmtilesProtocol(): void {
  if (pmtilesRegistered) return;
  const protocol = new Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  pmtilesRegistered = true;
}

export function authedTransformRequest(url: string): RequestParameters | undefined {
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

export function buildStyle(): StyleSpecification {
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
