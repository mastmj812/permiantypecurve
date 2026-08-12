// Pure helpers for the narvi planned-stick overlay on the main map:
// feature building (color + bench key precomputed per feature), the
// bench pick-list, and the per-bench visibility filter. Kept DB/map-free
// so vitest can cover the bench-key and filter semantics directly.

import type { FilterSpecification } from "maplibre-gl";

import type { NarviDealSticks } from "../api/narvi";
import { codeRank, colorForFormation } from "./formations";

export const NARVI_STICKS_SOURCE_ID = "narvi-sticks";
export const NARVI_STICKS_LINE_LAYER = "narvi-sticks-line";

// Bucket for wells narvi saved without a formation. They render in
// OTHER_COLOR and stay visible unless explicitly unchecked — a NULL
// bench must never be silently dropped.
export const UNSET_BENCH = "(unset)";

// Toggle/color key for a raw narvi formation code: strip the bimodal
// `_b` landing-target suffix (WCA_1_b -> WCA_1, mirroring the backend's
// intel_forecast._bench_code) so split benches share their base bench's
// color and checkbox. The raw value stays on the feature for display.
export function benchKey(formation: string | null | undefined): string {
  if (!formation) return UNSET_BENCH;
  return formation.endsWith("_b") ? formation.slice(0, -2) : formation;
}

interface StickProperties {
  color: string;
  formation: string | null; // raw, may carry _b
  formation_key: string; // benchKey(formation) — toggle/filter key
  category: string; // PUD / UPSIDE
  well_name: string;
  deal_id: string; // narvi deal_id = one DSU in practice
  scenario_id: string;
  scenario_name: string | null;
  kind: "legs" | "turn";
}

function parseGeometry(geojson: string | null): GeoJSON.Geometry | null {
  if (!geojson) return null;
  try {
    return JSON.parse(geojson) as GeoJSON.Geometry;
  } catch {
    return null; // legacy rows may carry null/odd geometry — skip, don't throw
  }
}

/**
 * One feature per well from `legs_geojson` (MultiLineString) plus one per
 * non-null `turn_geojson` (U-turn arc LineString) carrying the SAME
 * properties, so turn arcs show/hide with their well's bench toggle.
 */
export function buildNarviStickFeatures(
  sticks: NarviDealSticks,
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  for (const w of sticks.wells) {
    const key = benchKey(w.formation);
    const base: Omit<StickProperties, "kind"> = {
      color: colorForFormation(key === UNSET_BENCH ? null : key),
      formation: w.formation,
      formation_key: key,
      category: w.category,
      well_name: w.well_name,
      deal_id: w.deal_id,
      scenario_id: w.scenario_id,
      scenario_name: w.scenario_name,
    };
    const legs = parseGeometry(w.legs_geojson);
    if (legs) {
      features.push({ type: "Feature", geometry: legs, properties: { ...base, kind: "legs" } });
    }
    const turn = parseGeometry(w.turn_geojson);
    if (turn) {
      features.push({ type: "Feature", geometry: turn, properties: { ...base, kind: "turn" } });
    }
  }
  return { type: "FeatureCollection", features };
}

/** Distinct bench keys of the loaded deal, stratigraphic order
 * (codeRank), UNSET_BENCH last. */
export function benchKeysFor(sticks: NarviDealSticks): string[] {
  const keys = new Set<string>();
  for (const w of sticks.wells) keys.add(benchKey(w.formation));
  return [...keys].sort((a, b) => {
    if (a === UNSET_BENCH) return 1;
    if (b === UNSET_BENCH) return -1;
    return codeRank(a) - codeRank(b) || a.localeCompare(b);
  });
}

/**
 * Per-bench filter for the stick layer only. A bench missing from
 * `visibility` (or `true`) is shown — new benches and the NULL bucket
 * default to visible.
 */
export function narviBenchFilter(
  benchKeys: string[],
  visibility: Record<string, boolean>,
): FilterSpecification {
  const enabled = benchKeys.filter((k) => visibility[k] !== false);
  return [
    "in",
    ["get", "formation_key"],
    ["literal", enabled],
  ] as unknown as FilterSpecification;
}
