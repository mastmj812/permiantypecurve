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
import { fetchWellsticks, type WellstickFeatureCollection } from "../api/wells";

const SOURCE_ID = "review-wellsticks";
const LAYER_ID = "review-wellsticks-line";
const PERMIAN_CENTER: [number, number] = [-102.5, 32.0];

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
    layers: layers("protomaps", "light"),
  };
}

interface Props {
  // Forecasts for the wells in scope — used to compute EUR/ft and to
  // detect which wells have a fit at all. Parent passes the same list
  // it renders the table from.
  forecasts: ForecastRow[];
  // Set of api14s the engineer has excluded via the table checkboxes.
  // Excluded wells render gray rather than disappearing — keeps spatial
  // context as the engineer prunes the set.
  excludedApi14s: Set<string>;
}

export function ReviewMap({ forecasts, excludedApi14s }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  // Persistent popup instance. Reusing one popup across mousemoves avoids
  // the flicker of remove()/new Popup() per event.
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const [styleLoaded, setStyleLoaded] = useState(false);
  const [data, setData] = useState<WellstickFeatureCollection | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Oil forecast lookup keyed by api14. The table uses oil as the
  // headline stream (per the brief), so EUR/ft on the map is oil EUR
  // ÷ lateral ft. Gas and water aren't visualized here.
  const oilByApi = useMemo(() => {
    const m = new Map<string, ForecastRow>();
    for (const f of forecasts) {
      if (f.stream === "oil") m.set(f.api14, f);
    }
    return m;
  }, [forecasts]);

  const api14s = useMemo(
    () => Array.from(new Set(forecasts.map((f) => f.api14))),
    [forecasts],
  );

  // Fetch wellsticks whenever the in-scope api14 set changes. Joining
  // the api14 list into the cache key dedupes back-to-back fetches
  // when forecasts mutate but the api14 set is stable.
  const api14sKey = api14s.join(",");
  useEffect(() => {
    if (api14s.length === 0) {
      setData(null);
      return;
    }
    let cancelled = false;
    setError(null);
    fetchWellsticks(api14s)
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
  }, [api14sKey]);

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
        const f = oilByApi.get(feat.properties.api14);
        const eur = f?.eur ?? null;
        const lat = f?.well_lateral_ft ?? null;
        const eur_per_ft =
          eur != null && lat != null && lat > 0 ? eur / lat : null;
        const props: Record<string, unknown> = {
          ...feat.properties,
          excluded: excludedApi14s.has(feat.properties.api14),
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
  }, [data, oilByApi, excludedApi14s]);

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
  // api14s populates after the initial empty render. Without this, the
  // first render returns null (no container ref), the init effect runs
  // against a null ref, and subsequent renders don't re-trigger the
  // init — leaving the map slot blank.
  const shouldRender = api14s.length > 0;
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

    const existing = map.getSource(SOURCE_ID) as
      | maplibregl.GeoJSONSource
      | undefined;
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
          .setHTML(buildReviewPopupHtml(feat.properties as Record<string, unknown>))
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

  // Reset the auto-fit guard whenever the api14 set changes — a fresh
  // review-batch should re-frame to its own footprint.
  useEffect(() => {
    fittedRef.current = false;
  }, [api14sKey]);

  if (!shouldRender) return null;

  return (
    <section style={{ marginTop: 16 }}>
      <header style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 }}>
        <strong>Map</strong>
        <span className="muted" style={{ fontSize: 11 }}>
          wells colored by oil EUR / lateral ft — hover for well info
        </span>
        {error && <span className="muted err">{error}</span>}
      </header>
      <div
        style={{
          position: "relative",
          width: "100%",
          height: 420,
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

function fmtVolHtml(v: unknown): string {
  const n = Number(v);
  if (v == null || !Number.isFinite(n)) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toFixed(0);
}

function buildReviewPopupHtml(p: Record<string, unknown>): string {
  const headline = p.well_name ? escHtml(p.well_name) : escHtml(p.api14);
  const sub = p.well_name
    ? `<div class="mtt-api">API14 ${escHtml(p.api14)}</div>`
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
        <tr><td>Formation</td><td>${escHtml(p.formation)}</td></tr>
        <tr><td>Operator</td><td>${escHtml(p.operator)}</td></tr>
        <tr><td>Vintage</td><td>${escHtml(p.well_vintage_year)}</td></tr>
        <tr><td>Lateral</td><td>${fmtIntHtml(p.well_lateral_ft)} ft</td></tr>
        <tr><td>EUR (oil)</td><td>${fmtVolHtml(p.eur)} BBL</td></tr>
        <tr><td>EUR / ft</td><td>${eurPerFt}</td></tr>
      </table>
      ${excludedBadge}
    </div>`;
}
