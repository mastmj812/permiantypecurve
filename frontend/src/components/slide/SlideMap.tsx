// Slide-export map panel.
//
// Slimmer than the main MapView — no toolbar, no drawing, no popups,
// no cohort/selection state. Just the basemap + the cohort wells
// rendered in green, fit-bounded to the cohort, and INTERACTIVE: the
// engineer can drag / scroll-zoom to frame the shot before exporting.
// A hidden <img> is refreshed with a canvas snapshot on every map
// "idle", so whatever is on screen at export time is exactly what the
// PPTX capture (which reads the img src) and the browser print path
// (which swaps img for canvas via @media print CSS — live MapLibre
// canvases print as a black rectangle) both pick up.

import { useEffect, useRef, useState } from "react";
import maplibregl, {
  type LayerSpecification,
  type Map as MlMap,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { ACREAGE_COLOR, fetchDealPolygonGeoJSON } from "../../api/dealPolygons";
import { DEFAULT_FILTER_SPEC } from "../../api/types";
import { type WellDetailLite, tileUrlTemplate } from "../../api/wells";
import { dealVisibilityFilter } from "../../map/dealVisibility";
import { cohortLineFilter, WELLS_SOURCE_ID } from "../../map/wellsLayers";
import {
  BLOCKS_FILL_LAYER,
  BLOCKS_LABEL_LAYER,
  BLOCKS_LINE_LAYER,
  SECTIONS_FILL_LAYER,
  SECTIONS_LABEL_LAYER,
  SECTIONS_LINE_LAYER,
  authedTransformRequest,
  buildStyle,
  loadBlocks,
  loadSections,
  registerPmtilesProtocol,
} from "./mapShared";
import { useNearViewport } from "./useNearViewport";

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
  // Per-shapefile acreage visibility from the Map tab. When showDeals is
  // on, only the shapefiles toggled on here are drawn (default {} = all
  // visible), so the slide mirrors the Map tab's per-shapefile toggles.
  dealVisibility?: Record<string, boolean>;
  width?: number;
  height?: number;
  // Lazy mount: keep the live MapLibre instance only while the panel is
  // near the viewport; off-screen the map is torn down (releasing its
  // WebGL context) and the last idle snapshot shows in its place. The
  // dossier sets this — its many map panels otherwise blow the
  // browser's WebGL-context cap and OOM the tab. The single-map slide
  // pages keep the default eager mount.
  lazy?: boolean;
}

const COHORT_GREEN = "#16a34a";
const LAYER_SOLID = "slide-wells-lines-solid";

// Survey-grid overlays (blocks/sections) come from ./mapShared —
// shared with the dossier's ScenarioSlideMap.
const DEALS_SOURCE_ID = "slide-deal-polygons";
const DEALS_FILL_LAYER = "slide-deal-polygons-fill";
const DEALS_LINE_LAYER = "slide-deal-polygons-line";

async function loadDealPolygons(
  map: MlMap,
  dealVisibility: Record<string, boolean>,
): Promise<void> {
  if (map.getSource(DEALS_SOURCE_ID)) return;
  try {
    const fc = await fetchDealPolygonGeoJSON();
    if (fc.features.length === 0) return;
    map.addSource(DEALS_SOURCE_ID, {
      type: "geojson",
      data: fc as unknown as GeoJSON.FeatureCollection,
    });
    // Per-shapefile visibility: only draw the shapefiles toggled on in
    // the Map tab. Same filter the Map and Review maps use.
    const filter = dealVisibilityFilter(dealVisibility);
    // Translucent fill + crisp outline so the wells and section grid
    // remain readable underneath. Single shared ACREAGE_COLOR — same
    // as the Map and Review tabs.
    map.addLayer({
      id: DEALS_FILL_LAYER,
      type: "fill",
      source: DEALS_SOURCE_ID,
      filter,
      paint: {
        "fill-color": ACREAGE_COLOR,
        "fill-opacity": 0.14,
      },
    });
    map.addLayer({
      id: DEALS_LINE_LAYER,
      type: "line",
      source: DEALS_SOURCE_ID,
      filter,
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

// Default ~6.55" × 4.36" at 96 px/in to match the export's
// slide-level map placement (right column, right-aligned to mirror
// the chart column's left margin, full chart-stack height).
export function SlideMap({
  api10s,
  wellDetails,
  showBlocks = true,
  showSections = true,
  showDeals = true,
  dealVisibility = {},
  width = 629,
  height = 418,
  lazy = false,
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

      // Fit to the cohort — modest padding so the autozoom lands as
      // tight as the spread allows; the user can drag/scroll-zoom to
      // reframe before exporting (every idle refreshes the capture).
      // Never clobber a restored camera (the user's framing from
      // before a lazy teardown).
      const b = cam ? null : cohortBounds(wellDetails);
      if (b && !b.isEmpty()) {
        map.fitBounds(b, { padding: 24, duration: 0, maxZoom: 13 });
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
      if (showDeals) overlayPromises.push(loadDealPolygons(map, dealVisibility));
      // Idle-listener fires even when no overlays loaded — the wells
      // still need to paint before we snapshot.
      if (overlayPromises.length === 0) overlayPromises.push(Promise.resolve());
      void Promise.allSettled(overlayPromises).then(() => {
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
        // Refresh the capture img on EVERY idle from here on — the
        // map stays live and interactive, so each pan/scroll-zoom the
        // user makes re-snapshots once the tiles settle. The PPTX
        // export and the print path always read the latest view.
        const refresh = () => {
          try {
            const url = map.getCanvas().toDataURL("image/png");
            setSnapshot(url);
          } catch (e) {
            // CORS taint shouldn't happen here (same-origin tiles +
            // same-origin pmtiles + same-origin geojson), but log if
            // it does so the developer sees the cause of a blank map.
            console.error("slide map snapshot failed", e);
          }
        };
        map.on("idle", refresh);
        // Nudge the renderer in case the map was already idle when the
        // overlay sources got added — without this the first "idle"
        // can sit waiting forever if the new sources don't trigger a
        // redraw.
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
    // Cohort api10s + details are slide-time inputs, not live state;
    // `live` alone drives (lazy) mount/teardown. Eslint disabled
    // deliberately.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  return (
    <div ref={outerRef} className="slide-map" style={{ width, height }}>
      {/* The live canvas stays visible AND interactive — drag /
          scroll-zoom to frame the shot. The img below carries the
          latest idle-time snapshot: hidden on screen (CSS), it's what
          the PPTX capture reads (img.src) and what @media print swaps
          in (live MapLibre canvases print as a black rectangle). */}
      <div
        ref={containerRef}
        className="slide-map-canvas"
        style={{ width, height }}
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
            // Torn-down lazy panel: show the last snapshot in place of
            // the (removed) live canvas. CSS hides this img otherwise.
            display: live ? undefined : "block",
          }}
        />
      )}
    </div>
  );
}
