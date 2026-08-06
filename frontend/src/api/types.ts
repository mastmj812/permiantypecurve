// Mirrors the backend pydantic models. Keep these in sync with the
// /api/wells/* responses — when a field is added server-side, update here.

export type WellStatus = "PDP" | "PA" | "SI" | "TA" | "INACTIVE" | "UNKNOWN";

export interface FilterSpec {
  formations: string[];
  operators: string[];
  counties: string[];
  statuses: WellStatus[];
  first_prod_start: string | null; // ISO date
  first_prod_end: string | null;
  lateral_min_ft: number | null;
  lateral_max_ft: number | null;
  // Explicit API10 allow-list. When non-empty, only the map's wells
  // that match one of these api10s are shown. Pasted from an external
  // tool's well-list workflow.
  api10s: string[];
}

export const DEFAULT_FILTER_SPEC: FilterSpec = {
  formations: [],
  operators: [],
  counties: [],
  statuses: ["PDP"],
  first_prod_start: null,
  first_prod_end: null,
  lateral_min_ft: null,
  lateral_max_ft: null,
  api10s: [],
};

// GeoJSON Polygon geometry (NOT a Feature). Canonical home — wells.ts
// re-exports it for back-compat with existing imports.
export interface GeoJsonPolygon {
  type: "Polygon";
  coordinates: number[][][];
}

// ---------------- type-well build-up provenance ----------------
// Mirrors backend app/type_curves/reason_codes.py — keep in sync.

export const REVIEW_REASON_CODES = {
  outlier_profile: "Outlier production profile",
  data_quality: "Data quality",
  mechanical_downtime: "Mechanical / downtime",
  parent_child_spacing: "Parent-child / spacing",
  geology_landing: "Geology / landing zone",
  other: "Other",
} as const;

export type ReasonCode = keyof typeof REVIEW_REASON_CODES;

export interface ExclusionEntry {
  code: ReasonCode;
  note: string;
}

// The most recent lasso/box spatial select, kept so "Add staged" can
// attribute the staged wells to the polygon that produced them.
export interface LastDraw {
  kind: "polygon" | "bbox";
  // Box draws are converted to a 5-point Polygon ring at capture time,
  // so the whole provenance pipeline speaks Polygon only.
  polygon: GeoJsonPolygon;
  filters: FilterSpec;
  api10s: string[];
  at: string; // ISO timestamp of the draw
}

// One step in the cohort-building narrative. Mirrors SelectionEventIn.
export interface SelectionEvent {
  kind: "polygon" | "bbox" | "click_add" | "click_remove" | "manual_remove";
  at: string;
  api10s: string[];
  polygon?: GeoJsonPolygon | null;
  filters: FilterSpec | Record<string, never>;
}

// Assembled by the Review page's Aggregate button; consumed by the
// type-curve save so the backend can snapshot the build-up funnel.
export interface ProvenanceDraft {
  selection_events: SelectionEvent[];
  partition: {
    cutoff_months: number;
    short: string[];
    no_peak: string[];
  } | null;
  exclusions: Record<string, ExclusionEntry>;
  filter_snapshot: FilterSpec;
}

export interface SelectionSummary {
  count: number;
  median_lateral_ft: number | null;
  vintage_histogram: Record<number, number>;
  operators_top5: Array<[string, number]>;
  formations: Record<string, number>;
  exceeds_soft_cap: boolean;
  exceeds_hard_cap: boolean;
}

export interface SelectResponse {
  api10s: string[];
  summary: SelectionSummary;
  filter_echo: Record<string, unknown>;
}

export interface OperatorMatch {
  operator: string;
  count: number;
}

export interface FacetCount {
  value: string;
  count: number;
}

export interface FilterFacets {
  formations: FacetCount[];
  statuses: FacetCount[];
  counties: FacetCount[];
  lateral_ft_min: number | null;
  lateral_ft_max: number | null;
  first_prod_year_min: number | null;
  first_prod_year_max: number | null;
}

export interface WellDetail {
  api10: string;
  operator: string | null;
  formation: string | null;
  formation_blueox: string | null;
  basin_blueox: string | null;
  first_prod_date: string | null;
  vintage_year: number | null;
  lateral_ft: number | null;
  proppant_lbs: number | null;
  fluid_bbl: number | null;
  stages: number | null;
  tvd_ft: number | null;
  county: string | null;
  basin: string | null;
  status: WellStatus;
  sh_lat: number | null;
  sh_lon: number | null;
  bh_lat: number | null;
  bh_lon: number | null;
  last_synced_at: string | null;
}
