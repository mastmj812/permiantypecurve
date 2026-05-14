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
export type PageId = "map" | "forecast";

export interface MapState {
  // ---- top-level nav ----
  currentPage: PageId;
  setCurrentPage: (p: PageId) => void;
  // api14s carried across nav from map → forecast page
  forecastApi14s: string[];
  setForecastApi14s: (api14s: string[]) => void;

  // ---- filters ----
  filters: FilterSpec;
  setFormations: (formations: string[]) => void;
  setOperators: (operators: string[]) => void;
  setStatuses: (statuses: WellStatus[]) => void;
  setVintageRange: (start: string | null, end: string | null) => void;
  setLateralRange: (min: number | null, max: number | null) => void;
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
  showPlss: boolean;
  setShowPlss: (v: boolean) => void;
  showWellsticks: boolean;
  setShowWellsticks: (v: boolean) => void;
}

export const useMapStore = create<MapState>((set) => ({
  currentPage: "map",
  setCurrentPage: (currentPage) => set({ currentPage }),
  forecastApi14s: [],
  setForecastApi14s: (forecastApi14s) => set({ forecastApi14s }),

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

  showPlss: false,
  setShowPlss: (showPlss) => set({ showPlss }),
  showWellsticks: true,
  setShowWellsticks: (showWellsticks) => set({ showWellsticks }),
}));
