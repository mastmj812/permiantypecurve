// Spatial view for the Review tab. Renders the wells currently in
// scope as wellsticks colored by oil EUR/ft, so the engineer can see
// the geographic pattern of the forecast set at a glance. Excluded
// wells render gray; wells without an oil forecast or without a
// lateral length render mid-gray. Bounds auto-fit on first load.

import { useEffect, useMemo, useRef, useState } from "react";
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

import { type ForecastRow } from "../api/forecasts";
import { getStoredToken } from "../api/auth";
import { ACREAGE_COLOR, fetchDealPolygonGeoJSON } from "../api/dealPolygons";
import { fetchWellsticks, type WellstickFeatureCollection } from "../api/wells";
import { dealVisibilityFilter } from "../map/dealVisibility";
import { useMapStore } from "../store/mapStore";

const SOURCE_ID = "review-wellsticks";
const LAYER_ID = "review-wellsticks-line";
const PERMIAN_CENTER: [number, number] = [-102.5, 32.0];

// Drag-resize bounds for the map panel. The default must match the
// store's initial reviewMapHeight — double-click on the handle resets
// to it.
const MAP_MIN_HEIGHT = 180;
const MAP_DEFAULT_HEIGHT = 420;

// Block + Section overlay layers — same data + same zoom thresholds as
// MapView's overlays. Kept as separate source IDs (review-blocks /
// review-sections) so the two map components don't fight over a shared
// MapLibre source registration if they ever live in the DOM at once.
// Always-on here (no toolbar toggle); the Review map is a passive
// spatial-context view, not a workflow surface.
const BLOCKS_SOURCE_ID = "review-blocks";
const BLOCKS_FILL_LAYER = "review-blocks-fill";
const BLOCKS_LINE_LAYER = "review-blocks-line";
const BLOCKS_LABEL_LAYER = "review-blocks-label";
const BLOCKS_MIN_ZOOM = 8;

const SECTIONS_SOURCE_ID = "review-sections";
const SECTIONS_FILL_LAYER = "review-sections-fill";
const SECTIONS_LINE_LAYER = "review-sections-line";
const SECTIONS_LABEL_LAYER = "review-sections-label";
const SECTIONS_MIN_ZOOM = 11;

// Uploaded acreage polygons — always-on context layer, same as the
// Map tab. Fetched once from /api/deals/polygons.geojson and rendered
// in the shared ACREAGE_COLOR at every zoom.
const DEALS_SOURCE_ID = "review-deal-polygons";
const DEALS_FILL_LAYER = "review-deal-polygons-fill";
const DEALS_LINE_LAYER = "review-deal-polygons-line";

// Same label-key fallback chain MapView uses — OTLS GLO export columns
// vary in casing across vintages.
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

// Module-level guard mirrors MapView's pattern — addProtocol is safe to
// call more than once, but the boolean keeps the construction of a new
// Protocol() to once per page load.
let pmtilesRegistered = false;
function registerPmtilesProtocol() {
  if (pmtilesRegistered) return;
  const protocol = new Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  pmtilesRegistered = true;
}

// Same /api/* bearer-token injection MapView uses.
function authedTransformRequest(url: string): RequestParameters | undefined {
  try {
    const parsed = new URL(url, window.location.origin);
    if (
      parsed.origin === window.location.origin &&
      parsed.pathname.startsWith("/api/")
    ) {
      const token = getStoredToken();
      if (token) return { url, headers: { Authorization: `Bearer ${token}` } };
    }
  } catch {
    /* malformed URL — pass through */
  }
  return { url };
}

function buildStyle(): StyleSpecification {
  return {
    version: 8,
    glyphs:
      "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
    sources: {
      protomaps: {
        type: "vector",
        url: "pmtiles:///api/basemap/permian.pmtiles",
      },
    },
    layers: layers("protomaps", "light", "en"),
  };
}

interface Props {
  // Forecasts for the wells in scope — used to compute EUR/ft and to
  // detect which wells have a fit at all. Parent passes the same list
  // it renders the table from.
  forecasts: ForecastRow[];
  // Set of api10s the engineer has excluded via the table checkboxes.
  // Excluded wells render gray rather than disappearing — keeps spatial
  // context as the engineer prunes the set.
  excludedApi10s: Set<string>;
  // Currently-selected formation from the Review-tab dropdown. "all"
  // means no formation restriction; anything else narrows the map to
  // wells whose joined `formation` matches. Applied via MapLibre's
  // layer-level `filter` expression so changing it doesn't re-fetch
  // the wellsticks GeoJSON.
  formationFilter: string;
}

export function ReviewMap({ forecasts, excludedApi10s, formationFilter }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  // Persistent popup instance. Reusing one popup across mousemoves avoids
  // the flicker of remove()/new Popup() per event.
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const [styleLoaded, setStyleLoaded] = useState(false);
  const [data, setData] = useState<WellstickFeatureCollection | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Per-shapefile acreage visibility set on the Map tab. The Review map
  // mirrors those toggles rather than always showing every shapefile.
  const dealVisibility = useMapStore((s) => s.dealVisibility);
  // Panel height, adjustable by dragging the separator above the map.
  // Store-backed so the chosen size survives tab navigation.
  const mapHeight = useMapStore((s) => s.reviewMapHeight);
  const setMapHeight = useMapStore((s) => s.setReviewMapHeight);
  // Live drag bookkeeping — refs, not state: pointermove shouldn't
  // re-render anything beyond the height write itself.
  const dragRef = useRef<{ startY: number; startHeight: number } | null>(null);
  // Flips true once the acreage layers exist, so the visibility effect
  // knows it can call setFilter on them (they're added async post-fetch).
  const [dealsReady, setDealsReady] = useState(false);
  // Overlay-fetch errors are surfaced inline (small muted text in the
  // header) rather than as map-warning banners — the Review map is a
  // glance view, not a workflow surface, and a missing-GeoJSON message
  // doesn't need the same prominence it gets on the Map tab.
  const [blocksError, setBlocksError] = useState<string | null>(null);
  const [sectionsError, setSectionsError] = useState<string | null>(null);

  // Oil forecast lookup keyed by api10. The table uses oil as the
  // headline stream (per the brief), so EUR/ft on the map is oil EUR
  // ÷ lateral ft. Gas and water aren't visualized here.
  const oilByApi = useMemo(() => {
    const m = new Map<string, ForecastRow>();
    for (const f of forecasts) {
      if (f.stream === "oil") m.set(f.api10, f);
    }
    return m;
  }, [forecasts]);

  const api10s = useMemo(
    () => Array.from(new Set(forecasts.map((f) => f.api10))),
    [forecasts],
  );

  // Fetch wellsticks whenever the in-scope api10 set changes. Joining
  // the api10 list into the cache key dedupes back-to-back fetches
  // when forecasts mutate but the api10 set is stable.
  const api10sKey = api10s.join(",");
  useEffect(() => {
    if (api10s.length === 0) {
      setData(null);
      return;
    }
    let cancelled = false;
    setError(null);
    fetchWellsticks(api10s)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api10sKey]);

  // Join EUR/ft + excluded flag onto each feature so the MapLibre paint
  // expression can read them directly. Also stash the joined well/forecast
  // attributes the hover tooltip needs (name, vintage, lateral, EUR) so the
  // tooltip handler can read straight off `feature.properties` without a
  // separate lookup. Features without an oil forecast or with a null
  // lateral fall through to "no EUR" gray. Local feature type widens the
  // wire type's strict `properties` to a Record so the spread + dynamic
  // additions typecheck.
  type JoinedFeature = {
    type: "Feature";
    geometry: { type: "LineString"; coordinates: [number, number][] };
    properties: Record<string, unknown>;
  };
  type JoinedFeatureCollection = {
    type: "FeatureCollection";
    features: JoinedFeature[];
  };
  const joined = useMemo<JoinedFeatureCollection | null>(() => {
    if (!data) return null;
    return {
      type: "FeatureCollection",
      features: data.features.map((feat) => {
        const f = oilByApi.get(feat.properties.api10);
        const eur = f?.eur ?? null;
        const lat = f?.well_lateral_ft ?? null;
        const eur_per_ft =
          eur != null && lat != null && lat > 0 ? eur / lat : null;
        const props: Record<string, unknown> = {
          ...feat.properties,
          excluded: excludedApi10s.has(feat.properties.api10),
          well_name: f?.well_name ?? null,
          well_vintage_year: f?.well_vintage_year ?? null,
          well_lateral_ft: lat,
          eur,
        };
        if (eur_per_ft != null && Number.isFinite(eur_per_ft)) {
          props.eur_per_ft = eur_per_ft;
        }
        return { ...feat, properties: props };
      }),
    };
  }, [data, oilByApi, excludedApi10s]);

  // Adaptive color ramp. Fixed thresholds wouldn't translate across
  // plays (Permian unconventionals run 30–100 BBL/ft; conventional
  // verticals are much lower). Using the 5th/95th percentile of
  // in-scope EUR/ft anchors the ramp to the engineer's current
  // selection without letting one outlier blow out the dynamic range.
  const ramp = useMemo(() => {
    if (!joined) return null;
    const values = joined.features
      .map((f) => f.properties.eur_per_ft as number | undefined)
      .filter((v): v is number => v != null && Number.isFinite(v))
      .sort((a, b) => a - b);
    if (values.length === 0) return null;
    const pct = (p: number) =>
      values[Math.min(values.length - 1, Math.floor(p * values.length))]!;
    return {
      lo: pct(0.05),
      mid: pct(0.5),
      hi: pct(0.95),
    };
  }, [joined]);

  // -------------- init map (once the container is in the DOM) --------------
  // Keyed on `shouldRender` rather than `[]` so the effect re-fires when
  // api10s populates after the initial empty render. Without this, the
  // first render returns null (no container ref), the init effect runs
  // against a null ref, and subsequent renders don't re-trigger the
  // init — leaving the map slot blank.
  const shouldRender = api10s.length > 0;
  useEffect(() => {
    if (!shouldRender) return;
    if (!containerRef.current || mapRef.current) return;
    registerPmtilesProtocol();
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(),
      center: PERMIAN_CENTER,
      zoom: 7,
      minZoom: 4,
      maxZoom: 14,
      transformRequest: authedTransformRequest,
      attributionControl: false,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
    map.on("styledata", () => setStyleLoaded(true));
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      setStyleLoaded(false);
    };
  }, [shouldRender]);

  // -------------- attach / refresh the wellsticks source --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map || !joined) return;

    const existing = map.getSource<maplibregl.GeoJSONSource>(SOURCE_ID);
    if (existing) {
      existing.setData(joined);
    } else {
      map.addSource(SOURCE_ID, { type: "geojson", data: joined });
    }

    // Color expression: gray when excluded, gray when no EUR/ft, else
    // interpolate across the adaptive ramp.
    const colorExpr: ExpressionSpecification = ramp
      ? [
          "case",
          ["==", ["get", "excluded"], true],
          "#9ca3af",
          ["has", "eur_per_ft"],
          [
            "interpolate",
            ["linear"],
            ["get", "eur_per_ft"],
            ramp.lo,
            "#1e3a8a",
            ramp.mid,
            "#84cc16",
            ramp.hi,
            "#b91c1c",
          ],
          "#6b7280",
        ]
      : ["literal", "#6b7280"];

    if (!map.getLayer(LAYER_ID)) {
      map.addLayer({
        id: LAYER_ID,
        type: "line",
        source: SOURCE_ID,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": colorExpr,
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            7,
            1.5,
            10,
            2.5,
            14,
            4,
          ],
          "line-opacity": [
            "case",
            ["==", ["get", "excluded"], true],
            0.45,
            0.9,
          ],
        },
      });

      // Hover tooltip — wired once, right after the layer is created so
      // we don't double-bind on subsequent paint updates. Cleanup happens
      // implicitly when map.remove() runs in the init effect's teardown.
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
          .setHTML(buildReviewPopupHtml(feat.properties))
          .addTo(map);
      };
      const onLeave = () => {
        map.getCanvas().style.cursor = "";
        popupRef.current?.remove();
      };
      map.on("mousemove", LAYER_ID, onMove);
      map.on("mouseleave", LAYER_ID, onLeave);
    } else {
      map.setPaintProperty(LAYER_ID, "line-color", colorExpr);
    }
  }, [styleLoaded, joined, ramp]);

  // -------------- formation filter --------------
  // Applied via setFilter on the wellsticks line layer so toggling the
  // dropdown is instant — no wellsticks re-fetch and no source-data
  // mutation. "all" clears the filter; anything else restricts to
  // matching `formation` values. The hover tooltip already reads
  // properties off the queried feature, so it stays in sync.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map || !map.getLayer(LAYER_ID)) return;
    if (formationFilter === "all") {
      map.setFilter(LAYER_ID, null);
    } else {
      map.setFilter(LAYER_ID, [
        "==",
        ["get", "formation_blueox"],
        formationFilter,
      ]);
    }
  }, [styleLoaded, formationFilter, joined]);

  // -------------- Blocks overlay (zoom 8+) --------------
  // Always-on context layer. Fetch once on first styleLoaded; subsequent
  // re-renders just rely on the layer's existing zoom-gated visibility.
  // A 404 means the GeoJSON wasn't built; log inline rather than
  // banner-warning, since this is a glance view not a workflow surface.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map || map.getSource(BLOCKS_SOURCE_ID)) return;
    fetch("/api/basemap/blocks_tx_nm.geojson")
      .then((r) => {
        if (r.status === 404) {
          setBlocksError("blocks GeoJSON not built");
          return null;
        }
        if (!r.ok) throw new Error(`blocks fetch ${r.status}`);
        return r.json() as Promise<GeoJSON.GeoJSON>;
      })
      .then((geo) => {
        if (!geo) return;
        setBlocksError(null);
        map.addSource(BLOCKS_SOURCE_ID, { type: "geojson", data: geo });
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
  }, [styleLoaded]);

  // -------------- Sections overlay (zoom 11+) --------------
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map || map.getSource(SECTIONS_SOURCE_ID)) return;
    fetch("/api/basemap/sections_tx_nm.geojson")
      .then((r) => {
        if (r.status === 404) {
          setSectionsError("sections GeoJSON not built");
          return null;
        }
        if (!r.ok) throw new Error(`sections fetch ${r.status}`);
        return r.json() as Promise<GeoJSON.GeoJSON>;
      })
      .then((geo) => {
        if (!geo) return;
        setSectionsError(null);
        map.addSource(SECTIONS_SOURCE_ID, { type: "geojson", data: geo });
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
  }, [styleLoaded]);

  // -------------- Acreage polygons (uploaded shapefiles) --------------
  // Always-on overlay mirroring the Map tab. Fetch once on first
  // styleLoaded; a missing/empty set is a no-op. Rendered in the shared
  // ACREAGE_COLOR so the Review map matches the Map tab.
  useEffect(() => {
    if (!styleLoaded) return;
    const map = mapRef.current;
    if (!map || map.getSource(DEALS_SOURCE_ID)) return;
    let cancelled = false;
    fetchDealPolygonGeoJSON()
      .then((fc) => {
        if (cancelled || map.getSource(DEALS_SOURCE_ID)) return;
        if (!fc.features.length) return;
        map.addSource(DEALS_SOURCE_ID, {
          type: "geojson",
          data: fc as unknown as GeoJSON.FeatureCollection,
        });
        map.addLayer({
          id: DEALS_FILL_LAYER,
          type: "fill",
          source: DEALS_SOURCE_ID,
          paint: { "fill-color": ACREAGE_COLOR, "fill-opacity": 0.15 },
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
        setDealsReady(true);
      })
      .catch((e) => {
        console.warn("review acreage polygons fetch failed", e);
      });
    return () => {
      cancelled = true;
    };
  }, [styleLoaded]);

  // -------------- acreage per-shapefile visibility --------------
  // Mirror the Map tab's per-shapefile toggles: hide features whose
  // source_file is switched off. Re-applies whenever the toggles change
  // or the layers first become available. Same filter the Map tab uses,
  // so the two stay in lockstep.
  useEffect(() => {
    if (!styleLoaded || !dealsReady) return;
    const map = mapRef.current;
    if (!map || !map.getLayer(DEALS_FILL_LAYER)) return;
    const filter = dealVisibilityFilter(dealVisibility);
    map.setFilter(DEALS_FILL_LAYER, filter);
    map.setFilter(DEALS_LINE_LAYER, filter);
  }, [styleLoaded, dealsReady, dealVisibility]);

  // -------------- fit bounds once data arrives --------------
  const fittedRef = useRef(false);
  useEffect(() => {
    if (!styleLoaded || !joined || joined.features.length === 0) return;
    const map = mapRef.current;
    if (!map) return;
    // Only auto-fit on first load — re-fitting on every excluded
    // toggle would yank the view around as the engineer prunes.
    if (fittedRef.current) return;
    let minLon = Infinity;
    let minLat = Infinity;
    let maxLon = -Infinity;
    let maxLat = -Infinity;
    for (const feat of joined.features) {
      for (const [lon, lat] of feat.geometry.coordinates) {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
    }
    if (Number.isFinite(minLon)) {
      map.fitBounds(
        [
          [minLon, minLat],
          [maxLon, maxLat],
        ],
        { padding: 32, maxZoom: 12, animate: false },
      );
      fittedRef.current = true;
    }
  }, [styleLoaded, joined]);

  // Reset the auto-fit guard whenever the api10 set changes — a fresh
  // review-batch should re-frame to its own footprint.
  useEffect(() => {
    fittedRef.current = false;
  }, [api10sKey]);

  // MapLibre's trackResize observer picks up container size changes on
  // its own, but an explicit resize() right after the height write keeps
  // the canvas in lockstep with the drag instead of one observer tick
  // behind.
  useEffect(() => {
    mapRef.current?.resize();
  }, [mapHeight]);

  function onResizePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragRef.current = { startY: e.clientY, startHeight: mapHeight };
  }
  function onResizePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const d = dragRef.current;
    if (!d) return;
    // The handle sits ABOVE the map, so dragging up grows it. Clamp so
    // some of the table always stays reachable above the map.
    const max = Math.max(MAP_MIN_HEIGHT, window.innerHeight - 160);
    const next = Math.round(d.startHeight + (d.startY - e.clientY));
    setMapHeight(Math.min(max, Math.max(MAP_MIN_HEIGHT, next)));
  }
  function onResizePointerUp(e: React.PointerEvent<HTMLDivElement>) {
    dragRef.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
  }

  if (!shouldRender) return null;

  return (
    <section style={{ marginTop: 4, flexShrink: 0 }}>
      <div
        className="review-map-resizer"
        title="Drag to resize the map — double-click to reset"
        onPointerDown={onResizePointerDown}
        onPointerMove={onResizePointerMove}
        onPointerUp={onResizePointerUp}
        onDoubleClick={() => setMapHeight(MAP_DEFAULT_HEIGHT)}
      />
      <header style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 }}>
        <strong>Map</strong>
        <span className="muted" style={{ fontSize: 11 }}>
          wells colored by oil EUR / lateral ft — hover for well info
        </span>
        {error && <span className="muted err">{error}</span>}
        {blocksError && (
          <span className="muted err" style={{ fontSize: 11 }}>
            blocks: {blocksError}
          </span>
        )}
        {sectionsError && (
          <span className="muted err" style={{ fontSize: 11 }}>
            sections: {sectionsError}
          </span>
        )}
      </header>
      <div
        style={{
          position: "relative",
          width: "100%",
          height: mapHeight,
          border: "1px solid #e5e7eb",
          borderRadius: 4,
          overflow: "hidden",
        }}
      >
        <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
        <ReviewMapLegend ramp={ramp} />
      </div>
    </section>
  );
}

// Color-ramp + qualitative-bucket legend for the review map. Renders the
// same three stops the paint expression uses (lo/mid/hi) so the engineer
// can read a stick's color back into a BBL/ft value without checking the
// header text. Hidden until the ramp is computable (i.e. at least one
// in-scope well has an oil EUR / lateral ft).
function ReviewMapLegend({
  ramp,
}: {
  ramp: { lo: number; mid: number; hi: number } | null;
}) {
  return (
    <div className="map-legend">
      <div className="map-legend-title">Oil EUR / lateral ft</div>
      {ramp ? (
        <>
          <div
            className="map-legend-ramp"
            style={{
              background:
                "linear-gradient(to right, #1e3a8a 0%, #84cc16 50%, #b91c1c 100%)",
            }}
          />
          <div className="map-legend-ticks">
            <span>{ramp.lo.toFixed(0)}</span>
            <span>{ramp.mid.toFixed(0)}</span>
            <span>{ramp.hi.toFixed(0)}</span>
          </div>
          <div className="map-legend-unit">BBL / ft</div>
        </>
      ) : (
        <div className="map-legend-empty muted">no EUR in scope</div>
      )}
      <div className="map-legend-row">
        <span className="map-legend-swatch" style={{ background: "#6b7280" }} />
        no EUR / no lateral
      </div>
      <div className="map-legend-row">
        <span
          className="map-legend-swatch"
          style={{ background: "#9ca3af", opacity: 0.45 }}
        />
        excluded
      </div>
    </div>
  );
}

// Escape user-controlled strings before injecting into setHTML — well names
// and operator names come from the upstream data warehouse and could in
// principle contain HTML metacharacters.
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

function fmtVolHtml(v: unknown): string {
  const n = Number(v);
  if (v == null || !Number.isFinite(n)) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toFixed(0);
}

function buildReviewPopupHtml(p: Record<string, unknown>): string {
  const headline = p.well_name ? escHtml(p.well_name) : escHtml(p.api10);
  const sub = p.well_name
    ? `<div class="mtt-api">API10 ${escHtml(p.api10)}</div>`
    : "";
  const eurPerFt =
    p.eur_per_ft != null && Number.isFinite(Number(p.eur_per_ft))
      ? `${Number(p.eur_per_ft).toFixed(1)} BBL/ft`
      : "—";
  const excludedBadge =
    p.excluded === true
      ? `<div class="mtt-badge mtt-badge-excluded">EXCLUDED</div>`
      : "";
  return `
    <div class="mtt">
      <div class="mtt-name">${headline}</div>
      ${sub}
      <table class="mtt-table">
        <tr><td>Formation</td><td>${escHtml(p.formation_blueox)}${
          p.formation ? ` <span class="muted">(${escHtml(p.formation)})</span>` : ""
        }</td></tr>
        <tr><td>Operator</td><td>${escHtml(p.operator)}</td></tr>
        <tr><td>Vintage</td><td>${escHtml(p.well_vintage_year)}</td></tr>
        <tr><td>Lateral</td><td>${fmtIntHtml(p.well_lateral_ft)} ft</td></tr>
        <tr><td>EUR (oil)</td><td>${fmtVolHtml(p.eur)} BBL</td></tr>
        <tr><td>EUR / ft</td><td>${eurPerFt}</td></tr>
      </table>
      ${excludedBadge}
    </div>`;
}
