// Single Zustand store for map state. Two top-level slices:
//   * filters       — what the tile endpoint should render
//   * selection     — which api10s the user has picked, plus the drawer summary
//
// Components read from this store; tile-source URL derives from filters via a
// selector so MapLibre re-fetches tiles automatically when filters change.

import { create } from "zustand";

import type { DealPolygonGeoJSON } from "../api/dealPolygons";
import type { AggregatePayload } from "../api/typeCurves";
import {
  DEFAULT_FILTER_SPEC,
  type FilterSpec,
  type SelectionSummary,
  type WellStatus,
} from "../api/types";

export type DrawMode = "off" | "lasso" | "box" | "click";
export type PageId = "map" | "review" | "type_curve";

export interface MapState {
  // ---- top-level nav ----
  currentPage: PageId;
  setCurrentPage: (p: PageId) => void;
  // api10s carried across nav from map → forecast page
  forecastApi10s: string[];
  setForecastApi10s: (api10s: string[]) => void;
  // Snapshot of the EXACT well list the "Aggregate N into type curve"
  // button promised — the Review page's formation/outlier-FILTERED rows
  // minus exclusions, captured at click time. TypeCurvePage aggregates
  // this list when present. Without it, the TC page fell back to the
  // full forecastApi10s scope, silently building the curve from a
  // different (larger) well set than the button's count implied.
  // Cleared whenever a new forecast batch is set (a stale snapshot must
  // not override a fresh scope) — null means "use forecastApi10s".
  typeCurveApi10s: string[] | null;
  setTypeCurveApi10s: (api10s: string[] | null) => void;
  // Short-history cutoff (months post-peak) applied when the batch
  // autoforecast fires. Lives in the map store so the user sets it
  // BEFORE clicking Forecast — otherwise the field would only appear
  // on the destination page after the fit already started with
  // whatever value happened to be loaded. See app.forecasting.cohort.
  forecastCutoffMonths: number;
  setForecastCutoffMonths: (n: number) => void;
  // One-shot trigger: the Forecast button on the Map tab sets this to
  // true; the Review page consumes it (fires the batch) and clears it.
  // Tab navigation does NOT set it, so coming back to Review from
  // Type Curve or Map doesn't re-fire the batch.
  forecastTriggerPending: boolean;
  setForecastTriggerPending: (v: boolean) => void;
  // Partition produced by the most recent batch run. Persisted in the
  // store so the "pending transfer" badges + the cohort-transfer
  // button on Review survive tab navigation. Null when there's no
  // recent batch (fresh session) or after a successful transfer
  // cleared the short list.
  forecastPartition: {
    long: string[];
    short: string[];
    noPeak: string[];
    cutoffUsed: number;
  } | null;
  setForecastPartition: (p: MapState["forecastPartition"]) => void;
  // Same one-shot pattern as forecastTriggerPending — set by the
  // "Aggregate XX into type curve" button on Review, consumed by
  // TypeCurvePage. Plain tab nav doesn't set it, so coming back to
  // the TC tab doesn't recompute. Alignment changes ON the TC page
  // still recompute (user-driven, not nav-driven).
  typeCurveTriggerPending: boolean;
  setTypeCurveTriggerPending: (v: boolean) => void;
  // Persisted compute result so the TC tab shows the previous build
  // on nav-return instead of going blank. Cleared when the user
  // clicks Aggregate again (the next build overwrites it).
  typeCurveAgg: AggregatePayload | null;
  setTypeCurveAgg: (agg: AggregatePayload | null) => void;
  // Live fit status for the most recent batch — moved here from
  // ReviewPage local state so StrictMode's double-effect-run +
  // simulated unmount doesn't lose updates from the polling loop.
  // The polling can write to the store regardless of which mount
  // is currently rendering.
  fitStatus: "idle" | "running" | "succeeded" | "failed";
  fitStatusNote: string;
  setFitStatus: (s: MapState["fitStatus"]) => void;
  setFitStatusNote: (n: string) => void;
  // One-shot guard against the batch being kicked off twice (e.g.
  // StrictMode double-effect-run, or a click handler firing twice).
  // Set to the in-flight job_id when a batch starts; cleared when
  // the polling loop terminates. A second concurrent attempt to fire
  // a batch sees this is set and bails.
  fitJobId: string | null;
  setFitJobId: (id: string | null) => void;
  // Cohort-handoff metadata that rides alongside forecastApi10s when
  // the user clicks "Forecast" on the cohort bar. The Type Curve save
  // form pre-fills its name + deal dropdown from these when present;
  // both stay null when the user reaches Forecast via the legacy
  // lasso → "Forecast selected" right-rail flow. Cleared explicitly
  // when the user starts a new non-cohort selection.
  activeCohortName: string | null;
  activeCohortDealId: string | null;
  setCohortHandoff: (name: string | null, deal_id: string | null) => void;
  // Wells the engineer un-ticked on the Review page. Stays out of any
  // type-curve aggregation (step 6) but stays in the forecasts table.
  excludedApi10s: Set<string>;
  toggleExcluded: (api10: string) => void;
  setExcluded: (api10s: string[]) => void;
  clearExcluded: () => void;

  // ---- filters ----
  filters: FilterSpec;
  setFormations: (formations: string[]) => void;
  setOperators: (operators: string[]) => void;
  setStatuses: (statuses: WellStatus[]) => void;
  setVintageRange: (start: string | null, end: string | null) => void;
  setLateralRange: (min: number | null, max: number | null) => void;
  setApi10s: (api10s: string[]) => void;
  resetFilters: () => void;

  // ---- selection ----
  selectedApi10s: Set<string>;
  summary: SelectionSummary | null;
  setSelection: (api10s: string[], summary: SelectionSummary | null) => void;
  toggleApi10: (api10: string) => void;
  clearSelection: () => void;

  // ---- draw mode ----
  drawMode: DrawMode;
  setDrawMode: (mode: DrawMode) => void;

  // ---- layer toggles ----
  showBlocks: boolean;
  setShowBlocks: (v: boolean) => void;
  showSections: boolean;
  setShowSections: (v: boolean) => void;
  showWellsticks: boolean;
  setShowWellsticks: (v: boolean) => void;
  showBasementFaults: boolean;
  setShowBasementFaults: (v: boolean) => void;
  showSnfFaults: boolean;
  setShowSnfFaults: (v: boolean) => void;

  // ---- acreage polygons (uploaded shapefiles) ----
  // GeoJSON FeatureCollection cached after a fetch from
  // /api/deals/polygons.geojson. Null = not yet loaded.
  dealPolygons: DealPolygonGeoJSON | null;
  setDealPolygons: (fc: DealPolygonGeoJSON | null) => void;
  // Per-shapefile visibility. Keys are source_file names (the uploaded
  // .zip filename, or NO_SOURCE_FILE for legacy rows). Absent or true =
  // visible; the manage modal writes here and the Map tab filters
  // acreage features by it.
  dealVisibility: Record<string, boolean>;
  setDealVisibility: (sourceFile: string, visible: boolean) => void;

  // ---- TC add-wells mode ----
  // Set when the user lands on MapPage via the `#/type-curves/{id}/add-wells`
  // route. Replaces the cohort bar's normal "Add staged → cohort" flow
  // with an "Add N wells to TC: X" button that PATCHes the TC's
  // membership and returns to the workspace. Null in the normal map
  // flow (cohort staging) so existing UX is unaffected.
  tcAddWellsMode: {
    tcId: string;
    tcName: string;
    // Snapshot of the TC's current included_api10s — lets the panel
    // count "wells you're about to add (excluding those already in
    // the TC)" without re-fetching the TC on every selection change.
    existingApi10s: Set<string>;
  } | null;
  setTcAddWellsMode: (
    mode: { tcId: string; tcName: string; existingApi10s: Set<string> } | null,
  ) => void;
}

export const useMapStore = create<MapState>((set) => ({
  currentPage: "map",
  setCurrentPage: (currentPage) => set({ currentPage }),
  forecastApi10s: [],
  // A new forecast batch invalidates any Review-page aggregate
  // snapshot — otherwise a stale typeCurveApi10s from the previous
  // batch would silently override the fresh scope on the TC page.
  setForecastApi10s: (forecastApi10s) =>
    set({ forecastApi10s, typeCurveApi10s: null }),
  typeCurveApi10s: null,
  setTypeCurveApi10s: (typeCurveApi10s) => set({ typeCurveApi10s }),
  forecastCutoffMonths: 6,
  setForecastCutoffMonths: (forecastCutoffMonths) =>
    set({ forecastCutoffMonths }),
  forecastTriggerPending: false,
  setForecastTriggerPending: (forecastTriggerPending) =>
    set({ forecastTriggerPending }),
  forecastPartition: null,
  setForecastPartition: (forecastPartition) => set({ forecastPartition }),
  typeCurveTriggerPending: false,
  setTypeCurveTriggerPending: (typeCurveTriggerPending) =>
    set({ typeCurveTriggerPending }),
  typeCurveAgg: null,
  setTypeCurveAgg: (typeCurveAgg) => set({ typeCurveAgg }),
  fitStatus: "idle",
  fitStatusNote: "",
  setFitStatus: (fitStatus) => set({ fitStatus }),
  setFitStatusNote: (fitStatusNote) => set({ fitStatusNote }),
  fitJobId: null,
  setFitJobId: (fitJobId) => set({ fitJobId }),
  activeCohortName: null,
  activeCohortDealId: null,
  setCohortHandoff: (activeCohortName, activeCohortDealId) =>
    set({ activeCohortName, activeCohortDealId }),

  excludedApi10s: new Set<string>(),
  toggleExcluded: (api10) =>
    set((s) => {
      const next = new Set(s.excludedApi10s);
      if (next.has(api10)) next.delete(api10);
      else next.add(api10);
      return { excludedApi10s: next };
    }),
  setExcluded: (api10s) => set({ excludedApi10s: new Set(api10s) }),
  clearExcluded: () => set({ excludedApi10s: new Set<string>() }),

  filters: DEFAULT_FILTER_SPEC,
  setFormations: (formations) =>
    set((s) => ({ filters: { ...s.filters, formations } })),
  setOperators: (operators) =>
    set((s) => ({ filters: { ...s.filters, operators } })),
  setStatuses: (statuses) =>
    set((s) => ({ filters: { ...s.filters, statuses } })),
  setVintageRange: (start, end) =>
    set((s) => ({
      filters: { ...s.filters, first_prod_start: start, first_prod_end: end },
    })),
  setLateralRange: (min, max) =>
    set((s) => ({
      filters: { ...s.filters, lateral_min_ft: min, lateral_max_ft: max },
    })),
  setApi10s: (api10s) =>
    set((s) => ({ filters: { ...s.filters, api10s } })),
  resetFilters: () => set({ filters: DEFAULT_FILTER_SPEC }),

  selectedApi10s: new Set<string>(),
  summary: null,
  setSelection: (api10s, summary) =>
    set({ selectedApi10s: new Set(api10s), summary }),
  toggleApi10: (api10) =>
    set((s) => {
      const next = new Set(s.selectedApi10s);
      if (next.has(api10)) next.delete(api10);
      else next.add(api10);
      return { selectedApi10s: next };
    }),
  clearSelection: () => set({ selectedApi10s: new Set<string>(), summary: null }),

  drawMode: "off",
  setDrawMode: (drawMode) => set({ drawMode }),

  showBlocks: false,
  setShowBlocks: (showBlocks) => set({ showBlocks }),
  showSections: false,
  setShowSections: (showSections) => set({ showSections }),
  showWellsticks: true,
  setShowWellsticks: (showWellsticks) => set({ showWellsticks }),
  showBasementFaults: false,
  setShowBasementFaults: (showBasementFaults) => set({ showBasementFaults }),
  showSnfFaults: false,
  setShowSnfFaults: (showSnfFaults) => set({ showSnfFaults }),

  dealPolygons: null,
  setDealPolygons: (dealPolygons) => set({ dealPolygons }),
  dealVisibility: {},
  setDealVisibility: (sourceFile, visible) =>
    set((s) => ({
      dealVisibility: { ...s.dealVisibility, [sourceFile]: visible },
    })),

  tcAddWellsMode: null,
  setTcAddWellsMode: (tcAddWellsMode) => set({ tcAddWellsMode }),
}));
