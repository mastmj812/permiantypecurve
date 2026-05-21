// MapLibre layer + source IDs and paint config for the wells overlay.
//
// One vector source (`wells`) is fed by /api/wells/tiles/{z}/{x}/{y}.mvt and
// produces two source-layers depending on zoom:
//   * wells_points (z 3-8) → circles colored by formation
//   * wells_lines  (z 9+)  → linestrings, solid for heel_to_bh, dashed for surface_to_bh
//
// Selection highlighting is done via featureState so it updates without
// re-fetching tiles.

import type { ExpressionSpecification, LayerSpecification } from "maplibre-gl";

import { formationMatchPairs, OTHER_COLOR } from "./formations";

export const WELLS_SOURCE_ID = "wells";
export const WELLS_POINTS_LAYER = "wells-points";
export const WELLS_LINES_SOLID_LAYER = "wells-lines-solid";
export const WELLS_LINES_DASHED_LAYER = "wells-lines-dashed";
// Halo layers that paint a thicker sky-blue stroke under the regular
// wellsticks for wells in the active cohort. Driven by feature-state
// `cohort: true`, mirroring how `selected` drives the yellow selection
// halo. Two separate halo layers so heel_to_bh stays solid and
// surface_to_bh stays dashed; a separate points-zoom halo so cohort
// wells are visible at the low zooms where only circles render.
export const WELLS_POINTS_COHORT_LAYER = "wells-points-cohort";
export const WELLS_LINES_SOLID_COHORT_LAYER = "wells-lines-solid-cohort";
export const WELLS_LINES_DASHED_COHORT_LAYER = "wells-lines-dashed-cohort";

export const POINTS_MAXZOOM = 9;     // exclusive — switch to lines at 9
export const LINES_MINZOOM = 9;

const FORMATION_COLOR_EXPR: ExpressionSpecification = [
  "match",
  ["get", "formation"],
  ...formationMatchPairs(),
  OTHER_COLOR,
] as ExpressionSpecification;

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
  filter: ["==", ["get", "wellstick_source"], "heel_to_bh"],
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

// surface_to_bh wells are visually marked as "no survey confirmed" via dash.
export const wellsLinesDashedLayer: LayerSpecification = {
  id: WELLS_LINES_DASHED_LAYER,
  type: "line",
  source: WELLS_SOURCE_ID,
  "source-layer": "wells_lines",
  minzoom: LINES_MINZOOM,
  filter: ["==", ["get", "wellstick_source"], "surface_to_bh"],
  layout: { "line-cap": "butt", "line-join": "round" },
  paint: {
    "line-color": SELECTED_COLOR_EXPR,
    "line-width": [
      "interpolate",
      ["linear"],
      ["zoom"],
      9, 1.2,
      12, 2.5,
      14, 3.5,
    ],
    "line-dasharray": [2, 2],
    "line-opacity": 0.85,
  },
};

export const WELLS_INTERACTIVE_LAYERS = [
  WELLS_POINTS_LAYER,
  WELLS_LINES_SOLID_LAYER,
  WELLS_LINES_DASHED_LAYER,
];

// ---------------- cohort halo layers ----------------
// Matches the cohort-bar accent color so the bar and the map agree
// visually on "this well is in the active cohort".
const COHORT_HALO_COLOR = "#0ea5e9";

// Filter-based: layers are always present, but their filter restricts
// rendering to features whose api14 is in the active cohort. The
// MapView's cohort effect calls map.setFilter() whenever the cohort
// changes. We picked filter-based over feature-state because the
// halo needs to be unambiguously bound to api14 membership — no
// promote-id / timing surprises.
//
// Initial filter is the wellstick_source restriction ANDed with a
// literal false so no halos render until the cohort effect pushes
// the real api14 list.

export function cohortLineFilter(
  api14s: string[],
  wellstickSource: "heel_to_bh" | "surface_to_bh",
): ExpressionSpecification {
  return [
    "all",
    ["==", ["get", "wellstick_source"], wellstickSource],
    ["in", ["get", "api14"], ["literal", api14s]],
  ];
}

export function cohortPointFilter(
  api14s: string[],
): ExpressionSpecification {
  return ["in", ["get", "api14"], ["literal", api14s]];
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
  filter: cohortLineFilter([], "heel_to_bh"),
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

export const wellsLinesDashedCohortLayer: LayerSpecification = {
  id: WELLS_LINES_DASHED_COHORT_LAYER,
  type: "line",
  source: WELLS_SOURCE_ID,
  "source-layer": "wells_lines",
  minzoom: LINES_MINZOOM,
  filter: cohortLineFilter([], "surface_to_bh"),
  layout: { "line-cap": "butt", "line-join": "round" },
  paint: {
    "line-color": COHORT_HALO_COLOR,
    "line-width": [
      "interpolate", ["linear"], ["zoom"],
      9, 5.0,
      12, 8.5,
      14, 12.0,
    ],
    "line-opacity": 0.75,
  },
};
