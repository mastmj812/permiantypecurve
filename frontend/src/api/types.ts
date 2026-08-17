// Mirrors the backend pydantic models. Keep these in sync with the
// /api/wells/* responses — when a field is added server-side, update here.

export type WellStatus = "PDP" | "DUC" | "PA" | "SI" | "TA" | "INACTIVE" | "UNKNOWN";

// Novi WellSpacing no-neighbor sentinel: LateralCloserXY == 2800.0
// means "no same-zone neighbor at first production" (a cap, not a
// measurement). Mirrors backend wells_api/filters.py.
export const SPACING_SENTINEL_FT = 2800;

// Water-stream provenance classes (wells.water_source, synced from
// curated.water_data_quality) plus "no_data" for NULL (well absent
// from the matview). 'calculated' = the vendor water series is a
// formula (static WOR x oil), not measurement — the water fit
// inherits a fabricated stream. FLAG ONLY by convention of record
// (2026-08-17): badge + filter; nothing auto-excluded from any fit
// or cohort. Mirrors backend wells_api/filters.py.
export type WaterSourceClass =
  | "measured"
  | "calculated"
  | "indeterminate"
  | "insufficient"
  | "no_data";
export const WATER_SOURCE_CLASSES: WaterSourceClass[] = [
  "measured",
  "calculated",
  "indeterminate",
  "insufficient",
  "no_data",
];
export const WATER_SOURCE_LABELS: Record<WaterSourceClass, string> = {
  measured: "measured",
  calculated: "calculated (vendor WOR formula)",
  indeterminate: "indeterminate",
  insufficient: "insufficient history",
  no_data: "no water QC data",
};

export interface FilterSpec {
  formations: string[];
  operators: string[];
  counties: string[];
  statuses: WellStatus[];
  first_prod_start: string | null; // ISO date
  first_prod_end: string | null;
  lateral_min_ft: number | null;
  lateral_max_ft: number | null;
  // Same-zone spacing (Novi LateralCloserXY, ft, as-of-first-prod).
  // Three disjoint classes; the range binds only the first:
  //   measured     — real LateralCloserXY (non-null, != 2800)
  //   no_neighbor  — exactly the 2800 cap: known standalone
  //   no_data      — NULL: absent from Novi WellSpacing
  // Both class flags are live with or without a range — unchecking one
  // always removes wells from the map AND from lasso/box selection.
  spacing_min_ft: number | null;
  spacing_max_ft: number | null;
  spacing_include_no_neighbor: boolean;
  spacing_include_no_data: boolean;
  // Case-insensitive substring match on the well/lease name. Null/empty
  // = no filter. Metacharacters match literally (escaped server-side).
  well_name_contains: string | null;
  // Explicit API10 allow-list. When non-empty, only the map's wells
  // that match one of these api10s are shown. Pasted from an external
  // tool's well-list workflow.
  api10s: string[];
  // Water-provenance classes to admit. Empty = ALL classes (the
  // flag-only default — no filter on the wire). Non-empty = only wells
  // whose water_source is listed; "no_data" admits the NULLs.
  water_sources: WaterSourceClass[];
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
  spacing_min_ft: null,
  spacing_max_ft: null,
  // Default INCLUDED — the unfiltered map shows every well, same as
  // before the class split. Unchecking is what removes wells.
  spacing_include_no_neighbor: true,
  spacing_include_no_data: true,
  well_name_contains: null,
  api10s: [],
  water_sources: [],
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
  // v2: manual_remove events may carry the coded reason given at removal
  // time. Optional/additive — pre-v2 events simply lack it, so no
  // cohortStore persist-version bump.
  reason?: ExclusionEntry | null;
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
  // v2: coded map-curation removals distilled from the event narrative
  // (manualExclusionsFromEvents) — they attribute the not_selected stage.
  manual_exclusions: Record<string, ExclusionEntry>;
  // v2: the filter panel's formation scope at draw time (universe scope,
  // recorded but never a cull stage). Null → server infers from the
  // final included wells, as before.
  formations: string[] | null;
}

// ---------------- live build-up preview (Buildup drawer) ----------------
// Mirrors BuildupPreview* in app/api/type_curves.py.

export interface BuildupWaterfallRow {
  stage: string;
  description: string;
  culled: number;
  remaining: number;
}

export interface BuildupPreviewRow {
  api10: string;
  name: string | null;
  operator: string | null;
  formation: string | null;
  first_prod_date: string | null;
  lateral_ft: number | null;
  status: string | null;
  lateral_closer_xy_ft: number | null;
  disposition: string;
  reason_code: string | null;
  reason_label: string | null;
  note: string | null;
  // `included` rows only: the filter stage the CURRENT left-rail spec
  // would cull this well at. The well IS in the cohort — advisory, not
  // a cull, so it never moves a waterfall count.
  off_filter_stage: string | null;
}

export interface BuildupPreview {
  universe_count: number;
  universe_truncated: boolean;
  computed_at: string | null;
  formations: string[];
  aoi_polygon_count: number;
  aoi_total_area_sq_mi: number | null;
  waterfall: BuildupWaterfallRow[];
  rows: BuildupPreviewRow[];
  included_count: number;
  reconciles: boolean;
  notes: string[];
  no_aoi: boolean;
  // Cohort members the current filters would cull — the drop-list
  // behind the drawer's "drop off-filter wells" action.
  off_filter_api10s: string[];
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
  // Water-stream provenance (wells.water_source / wor_cv). FLAG ONLY.
  water_source: string | null;
  wor_cv: number | null;
  last_synced_at: string | null;
}
