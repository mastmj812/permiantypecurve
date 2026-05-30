// MapLibre layer + source IDs and paint config for the wells overlay.
//
// One vector source (`wells`) is fed by /api/wells/tiles/{z}/{x}/{y}.mvt and
// produces two source-layers depending on zoom:
//   * wells_points (z 3-8) → circles colored by formation
//   * wells_lines  (z 9+)  → linestrings (single Novi-sourced wellstick)
//
// Selection highlighting is done via featureState so it updates without
// re-fetching tiles.

import type { ExpressionSpecification, LayerSpecification } from "maplibre-gl";

import { formationMatchPairs, OTHER_COLOR } from "./formations";

export const WELLS_SOURCE_ID = "wells";
export const WELLS_POINTS_LAYER = "wells-points";
export const WELLS_LINES_SOLID_LAYER = "wells-lines-solid";
// Halo layers that paint a thicker sky-blue stroke under the regular
// wellsticks for wells in the active cohort. Filter-based — the
// MapView's cohort effect calls map.setFilter() whenever the active
// cohort changes. A separate points-zoom halo so cohort wells are
// visible at the low zooms where only circles render.
export const WELLS_POINTS_COHORT_LAYER = "wells-points-cohort";
export const WELLS_LINES_SOLID_COHORT_LAYER = "wells-lines-solid-cohort";

export const POINTS_MAXZOOM = 9;     // exclusive — switch to lines at 9
export const LINES_MINZOOM = 9;

// Cast via `unknown` because TS can't statically prove the spread of
// formationMatchPairs() satisfies MapLibre's recursive
// ExpressionSpecification union — but at runtime MapLibre validates
// the JSON and we already encode the (input, output) pair convention
// the spec requires.
const FORMATION_COLOR_EXPR: ExpressionSpecification = [
  "match",
  ["get", "formation"],
  ...formationMatchPairs(),
  OTHER_COLOR,
] as unknown as ExpressionSpecification;

// `feature-state.selected === true` → bright yellow halo; else formation color.
const SELECTED_COLOR_EXPR: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "selected"], false],
  "#facc15",
  FORMATION_COLOR_EXPR,
];

export const wellsPointsLayer: LayerSpecification = {
  id: WELLS_POINTS_LAYER,
  type: "circle",
  source: WELLS_SOURCE_ID,
  "source-layer": "wells_points",
  maxzoom: POINTS_MAXZOOM,
  paint: {
    "circle-radius": [
      "interpolate",
      ["linear"],
      ["zoom"],
      3, 1.5,
      6, 2.5,
      8, 4,
    ],
    "circle-color": SELECTED_COLOR_EXPR,
    "circle-stroke-width": [
      "case",
      ["boolean", ["feature-state", "selected"], false],
      2,
      0.5,
    ],
    "circle-stroke-color": "#111827",
    "circle-opacity": 0.9,
  },
};

export const wellsLinesSolidLayer: LayerSpecification = {
  id: WELLS_LINES_SOLID_LAYER,
  type: "line",
  source: WELLS_SOURCE_ID,
  "source-layer": "wells_lines",
  minzoom: LINES_MINZOOM,
  layout: { "line-cap": "round", "line-join": "round" },
  paint: {
    "line-color": SELECTED_COLOR_EXPR,
    "line-width": [
      "interpolate",
      ["linear"],
      ["zoom"],
      9, 1.5,
      12, 3.0,
      14, 4.5,
    ],
    "line-opacity": 0.95,
  },
};

export const WELLS_INTERACTIVE_LAYERS = [
  WELLS_POINTS_LAYER,
  WELLS_LINES_SOLID_LAYER,
];

// ---------------- cohort halo layers ----------------
// Matches the cohort-bar accent color so the bar and the map agree
// visually on "this well is in the active cohort".
const COHORT_HALO_COLOR = "#0ea5e9";

// Filter-based: layers are always present, but their filter restricts
// rendering to features whose api10 is in the active cohort. The
// MapView's cohort effect calls map.setFilter() whenever the cohort
// changes. We picked filter-based over feature-state because the
// halo needs to be unambiguously bound to api10 membership — no
// promote-id / timing surprises.

export function cohortLineFilter(api10s: string[]): ExpressionSpecification {
  return ["in", ["get", "api10"], ["literal", api10s]];
}

export function cohortPointFilter(api10s: string[]): ExpressionSpecification {
  return ["in", ["get", "api10"], ["literal", api10s]];
}

export const wellsPointsCohortLayer: LayerSpecification = {
  id: WELLS_POINTS_COHORT_LAYER,
  type: "circle",
  source: WELLS_SOURCE_ID,
  "source-layer": "wells_points",
  maxzoom: POINTS_MAXZOOM,
  filter: cohortPointFilter([]),
  paint: {
    "circle-radius": [
      "interpolate", ["linear"], ["zoom"],
      3, 4.5,
      6, 6.5,
      8, 9.0,
    ],
    "circle-color": COHORT_HALO_COLOR,
    "circle-opacity": 0.6,
    "circle-stroke-width": 0,
  },
};

export const wellsLinesSolidCohortLayer: LayerSpecification = {
  id: WELLS_LINES_SOLID_COHORT_LAYER,
  type: "line",
  source: WELLS_SOURCE_ID,
  "source-layer": "wells_lines",
  minzoom: LINES_MINZOOM,
  filter: cohortLineFilter([]),
  layout: { "line-cap": "round", "line-join": "round" },
  paint: {
    "line-color": COHORT_HALO_COLOR,
    "line-width": [
      "interpolate", ["linear"], ["zoom"],
      9, 6.0,
      12, 10.0,
      14, 14.0,
    ],
    "line-opacity": 0.85,
  },
};
