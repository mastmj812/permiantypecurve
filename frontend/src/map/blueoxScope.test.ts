// Scope upkeep when a narvi selection is deselected in the drop modal
// (gatorTails 2026-10-20: dsu_44_5_8 swapped for theCan_44_bs left a
// zone scoped to the removed pair → invisible "1 of N · 0w" + save 422).

import { describe, expect, it } from "vitest";

import type { BlueOxScenarioRef, BlueOxZoneSpec } from "../api/deals";
import {
  danglingScopeRefs,
  dropScopeRef,
  liveScopeRefs,
  pruneZoneScopes,
} from "./blueoxScope";
import { resolveZone } from "./blueoxZones";

const ref = (scenario_id: string): BlueOxScenarioRef => ({ deal_id: "gator", scenario_id });

const zone = (
  name: string,
  benches: string[],
  scope: string[] | null,
): BlueOxZoneSpec => ({
  type_curve_id: "00000000-0000-0000-0000-000000000000",
  zone_name: name,
  reserve_category: "PUD",
  benches,
  scenario_scope: scope ? scope.map(ref) : null,
});

describe("pruneZoneScopes", () => {
  it("strips a deselected ref from every scope that still has a live ref", () => {
    const zones = [
      zone("WCB_2_W", ["WCB_2"], ["west_a", "west_b"]),
      zone("WCB_2_E", ["WCB_2"], ["east", "west_b"]),
    ];
    const out = pruneZoneScopes(zones, [ref("west_a"), ref("east")]);
    expect(out[0]!.scenario_scope).toEqual([ref("west_a")]);
    expect(out[1]!.scenario_scope).toEqual([ref("east")]);
    for (const z of out) expect(danglingScopeRefs(z, [ref("west_a"), ref("east")])).toEqual([]);
  });

  it("never prunes a scope to empty/null — the zone is left dangling instead", () => {
    const zones = [zone("BS_3", ["BS_3"], ["dsu_44_5_8"])];
    const sel = [ref("theCan_44_bs"), ref("other")];
    const out = pruneZoneScopes(zones, sel);
    // not [] and not null: either would read as "all" and widen the zone
    expect(out[0]!.scenario_scope).toEqual([ref("dsu_44_5_8")]);
    expect(danglingScopeRefs(out[0]!, sel)).toEqual([ref("dsu_44_5_8")]);
    expect(liveScopeRefs(out[0]!, sel)).toEqual([]);
    // and the dangling zone captures none of the remaining selections
    for (const s of sel) expect(resolveZone("BS_3", s, out)).toBeNull();
  });

  it("re-selecting the scenario heals a dangling zone", () => {
    const sel = [ref("theCan_44_bs")];
    const out = pruneZoneScopes([zone("BS_3", ["BS_3"], ["dsu_44_5_8"])], sel);
    const healed = [...sel, ref("dsu_44_5_8")];
    expect(danglingScopeRefs(out[0]!, healed)).toEqual([]);
    expect(resolveZone("BS_3", ref("dsu_44_5_8"), out)?.zone.zone_name).toBe("BS_3");
  });

  it("leaves unscoped and fully-live zones untouched (same object)", () => {
    const zones = [
      zone("WCA", ["WCA_1"], null),
      { ...zone("WCB", ["WCB_1"], null), scenario_scope: [] },
      zone("BS_2", ["BS_2"], ["a", "b"]),
    ];
    const out = pruneZoneScopes(zones, [ref("a"), ref("b")]);
    out.forEach((z, i) => expect(z).toBe(zones[i]));
  });

  it("keeps an explicit scope explicit when it now equals every selection", () => {
    // [a,b] of {a,b,c} → deselect c → stays [a,b], not null: a later
    // selection d must not be silently swept into this zone
    const out = pruneZoneScopes([zone("BS_2", ["BS_2"], ["a", "b", "c"])], [ref("a"), ref("b")]);
    expect(out[0]!.scenario_scope).toEqual([ref("a"), ref("b")]);
    expect(resolveZone("BS_2", ref("d"), out)).toBeNull();
  });
});

describe("dropScopeRef", () => {
  it("drops one dangling ref when others remain", () => {
    const z = zone("BS_3", ["BS_3"], ["gone_1", "gone_2"]);
    expect(dropScopeRef(z, ref("gone_1"))?.scenario_scope).toEqual([ref("gone_2")]);
  });

  it("refuses to drop the last ref (empty scope = all scenarios)", () => {
    expect(dropScopeRef(zone("BS_3", ["BS_3"], ["gone"]), ref("gone"))).toBeNull();
  });
});
