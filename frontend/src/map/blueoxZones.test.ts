// Mirror of the backend routing semantics in _fetch_narvi_by_zone —
// the dossier's assignment coloring must agree with what the workbook
// ships (backend tests: test_blueox_export.py scenario-scope block).

import { describe, expect, it } from "vitest";

import type { BlueOxZoneSpec } from "../api/deals";
import { UNASSIGNED_COLOR, resolveZone, zoneColor } from "./blueoxZones";

const zone = (
  name: string,
  benches: string[],
  scope: Array<[string, string]> | null,
): BlueOxZoneSpec => ({
  type_curve_id: "00000000-0000-0000-0000-000000000000",
  zone_name: name,
  reserve_category: "PUD",
  benches,
  scenario_scope: scope
    ? scope.map(([deal_id, scenario_id]) => ({ deal_id, scenario_id }))
    : null,
});

describe("resolveZone", () => {
  const zones = [
    zone("bs1_s west", ["BS1_S"], [["alch", "plan_west_a"], ["alch", "plan_west_b"]]),
    zone("bs1_s east", ["BS1_S"], [["alch", "plan_east"]]),
    zone("wca", ["WCA_1", "WCA_2"], null), // unscoped = all scenarios
  ];

  it("routes the same bench to different zones by scenario", () => {
    const west = resolveZone("BS1_S", { deal_id: "alch", scenario_id: "plan_west_a" }, zones);
    const east = resolveZone("BS1_S", { deal_id: "alch", scenario_id: "plan_east" }, zones);
    expect(west?.zone.zone_name).toBe("bs1_s west");
    expect(east?.zone.zone_name).toBe("bs1_s east");
    expect(west?.index).toBe(0);
    expect(east?.index).toBe(1);
  });

  it("unscoped zones cover every scenario", () => {
    for (const s of ["plan_west_a", "plan_east", "plan_other"]) {
      const rz = resolveZone("WCA_2", { deal_id: "alch", scenario_id: s }, zones);
      expect(rz?.zone.zone_name).toBe("wca");
    }
  });

  it("returns null for uncovered scenarios, unmapped benches, null bench", () => {
    expect(
      resolveZone("BS1_S", { deal_id: "alch", scenario_id: "plan_other" }, zones),
    ).toBeNull();
    expect(
      resolveZone("WDFD", { deal_id: "alch", scenario_id: "plan_east" }, zones),
    ).toBeNull();
    expect(resolveZone(null, { deal_id: "alch", scenario_id: "plan_east" }, zones)).toBeNull();
  });

  it("empty-array scope behaves like null (all scenarios)", () => {
    const z = [zone("wca", ["WCA_1"], null)];
    z[0]!.scenario_scope = [];
    const rz = resolveZone("WCA_1", { deal_id: "alch", scenario_id: "anything" }, z);
    expect(rz?.zone.zone_name).toBe("wca");
  });
});

describe("zoneColor", () => {
  it("is stable per index, distinct across the first zones, never the unassigned gray", () => {
    expect(zoneColor(0)).toBe(zoneColor(0));
    const first = new Set([0, 1, 2, 3, 4].map(zoneColor));
    expect(first.size).toBe(5);
    for (const i of [0, 1, 2, 3, 4]) expect(zoneColor(i)).not.toBe(UNASSIGNED_COLOR);
  });
});
