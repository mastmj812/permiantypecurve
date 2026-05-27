// Single Zustand store for map state. Two top-level slices:
//   * filters       — what the tile endpoint should render
//   * selection     — which api14s the user has picked, plus the drawer summary
//
// Components read from this store; tile-source URL derives from filters via a
// selector so MapLibre re-fetches tiles automatically when filters change.

import { create } from "zustand";

import {
  DEFAULT_FILTER_SPEC,
  type FilterSpec,
  type SelectionSummary,
  type WellStatus,
} from "../api/types";

export type DrawMode = "off" | "lasso" | "box" | "click";
export type PageId = "map" | "forecast" | "review" | "type_curve";

export interface MapState {
  // ---- top-level nav ----
  currentPage: PageId;
  setCurrentPage: (p: PageId) => void;
  // api14s carried across nav from map → forecast page
  forecastApi14s: string[];
  setForecastApi14s: (api14s: string[]) => void;
  // Cohort-handoff metadata that rides alongside forecastApi14s when
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
  excludedApi14s: Set<string>;
  toggleExcluded: (api14: string) => void;
  setExcluded: (api14s: string[]) => void;
  clearExcluded: () => void;

  // ---- filters ----
  filters: FilterSpec;
  setFormations: (formations: string[]) => void;
  setOperators: (operators: string[]) => void;
  setStatuses: (statuses: WellStatus[]) => void;
  setVintageRange: (start: string | null, end: string | null) => void;
  setLateralRange: (min: number | null, max: number | null) => void;
  setApi14s: (api14s: string[]) => void;
  resetFilters: () => void;

  // ---- selection ----
  selectedApi14s: Set<string>;
  summary: SelectionSummary | null;
  setSelection: (api14s: string[], summary: SelectionSummary | null) => void;
  toggleApi14: (api14: string) => void;
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

  // ---- TC add-wells mode ----
  // Set when the user lands on MapPage via the `#/type-curves/{id}/add-wells`
  // route. Replaces the cohort bar's normal "Add staged → cohort" flow
  // with an "Add N wells to TC: X" button that PATCHes the TC's
  // membership and returns to the workspace. Null in the normal map
  // flow (cohort staging) so existing UX is unaffected.
  tcAddWellsMode: {
    tcId: string;
    tcName: string;
    // Snapshot of the TC's current included_api14s — lets the panel
    // count "wells you're about to add (excluding those already in
    // the TC)" without re-fetching the TC on every selection change.
    existingApi14s: Set<string>;
  } | null;
  setTcAddWellsMode: (
    mode: { tcId: string; tcName: string; existingApi14s: Set<string> } | null,
  ) => void;
}

export const useMapStore = create<MapState>((set) => ({
  currentPage: "map",
  setCurrentPage: (currentPage) => set({ currentPage }),
  forecastApi14s: [],
  setForecastApi14s: (forecastApi14s) => set({ forecastApi14s }),
  activeCohortName: null,
  activeCohortDealId: null,
  setCohortHandoff: (activeCohortName, activeCohortDealId) =>
    set({ activeCohortName, activeCohortDealId }),

  excludedApi14s: new Set<string>(),
  toggleExcluded: (api14) =>
    set((s) => {
      const next = new Set(s.excludedApi14s);
      if (next.has(api14)) next.delete(api14);
      else next.add(api14);
      return { excludedApi14s: next };
    }),
  setExcluded: (api14s) => set({ excludedApi14s: new Set(api14s) }),
  clearExcluded: () => set({ excludedApi14s: new Set<string>() }),

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
  setApi14s: (api14s) =>
    set((s) => ({ filters: { ...s.filters, api14s } })),
  resetFilters: () => set({ filters: DEFAULT_FILTER_SPEC }),

  selectedApi14s: new Set<string>(),
  summary: null,
  setSelection: (api14s, summary) =>
    set({ selectedApi14s: new Set(api14s), summary }),
  toggleApi14: (api14) =>
    set((s) => {
      const next = new Set(s.selectedApi14s);
      if (next.has(api14)) next.delete(api14);
      else next.add(api14);
      return { selectedApi14s: next };
    }),
  clearSelection: () => set({ selectedApi14s: new Set<string>(), summary: null }),

  drawMode: "off",
  setDrawMode: (drawMode) => set({ drawMode }),

  showBlocks: false,
  setShowBlocks: (showBlocks) => set({ showBlocks }),
  showSections: false,
  setShowSections: (showSections) => set({ showSections }),
  showWellsticks: true,
  setShowWellsticks: (showWellsticks) => set({ showWellsticks }),

  tcAddWellsMode: null,
  setTcAddWellsMode: (tcAddWellsMode) => set({ tcAddWellsMode }),
}));
