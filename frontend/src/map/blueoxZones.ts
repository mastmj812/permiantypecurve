// Blue Ox zone resolution for display — which zone (and therefore
// which type curve) captures a given well. TS mirror of the backend's
// routing in app/api/deals.py::_fetch_narvi_by_zone: first claiming
// zone whose scenario scope covers the well's scenario wins (the
// backend's overlap guard guarantees uniqueness); an unscoped zone
// covers every scenario. Keep the two in sync — this drives the
// dossier's curve-assignment coloring, and a divergence would show the
// engineer a different assignment than the workbook ships.

import type { BlueOxScenarioRef, BlueOxZoneSpec } from "../api/deals";

export interface ResolvedZone {
  index: number; // position in cfg.zones — keys the color palette
  zone: BlueOxZoneSpec;
}

function scopeCovers(
  scope: BlueOxScenarioRef[] | null | undefined,
  ref: BlueOxScenarioRef,
): boolean {
  if (!scope || scope.length === 0) return true; // unscoped = all
  return scope.some(
    (s) => s.deal_id === ref.deal_id && s.scenario_id === ref.scenario_id,
  );
}

/** The zone that captures (bench, scenario), or null (unassigned /
 * excluded benches resolve to null — the map paints those gray). */
export function resolveZone(
  bench: string | null | undefined,
  ref: BlueOxScenarioRef,
  zones: BlueOxZoneSpec[],
): ResolvedZone | null {
  if (!bench) return null;
  for (let i = 0; i < zones.length; i++) {
    const z = zones[i]!;
    if (z.benches.includes(bench) && scopeCovers(z.scenario_scope, ref)) {
      return { index: i, zone: z };
    }
  }
  return null;
}

// Categorical palette for zone coloring — distinct at small sizes on
// the light basemap, deliberately NOT the formation palette so a
// curve-assignment map can't be misread as a formation map.
const ZONE_COLORS: readonly string[] = [
  "#2563eb", // blue
  "#dc2626", // red
  "#16a34a", // green
  "#9333ea", // purple
  "#ea580c", // orange
  "#0891b2", // cyan
  "#ca8a04", // dark yellow
  "#db2777", // pink
  "#4d7c0f", // olive
  "#7c3aed", // violet
];

export const UNASSIGNED_COLOR = "#9ca3af"; // gray — no zone captures it

export function zoneColor(index: number): string {
  return ZONE_COLORS[index % ZONE_COLORS.length]!;
}
