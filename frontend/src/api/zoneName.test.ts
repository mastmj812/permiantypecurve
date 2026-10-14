// Golden tables copied verbatim from backend/app/exports/zone_name.py
// (GOLDEN_CASES / DEFAULT_CASES) — keep the two in step.
import { describe, expect, it } from "vitest";

import { defaultZoneName, zoneNameError } from "./zoneName";

const GOLDEN: [string, string[], boolean][] = [
  ["WCB_2", ["WCB_2"], true],
  ["WCB_2_NW", ["WCB_2"], true],
  ["WCB_2_N", ["WCB_2"], true],
  ["BS3_C_toucan", ["BS3_C"], true],
  ["WDFD_south", ["WDFD"], true],
  ["WCA", ["WCA_1"], true],
  ["AVA", ["AVA_1"], true],
  ["WCA_1", ["WCA_1", "WCA_2"], true],
  ["BS2_S", ["BS2_S", "BS2_C"], true],
  ["WCB_2 West", ["WCB_2"], true],
  ["W CB_2", ["WCB_2"], false],
  ["WCB_2 ", ["WCB_2"], false],
  ["WCB_2-A", ["WCB_2"], false],
  ["WCB_2__N", ["WCB_2"], false],
  ["WCB_2_", ["WCB_2"], false],
  ["WCB_2_v2", ["WCB_2"], false],
  ["snare_wcb_2_v2", ["WCB_2"], false],
  ["WCB_1", ["WCB_2"], false],
  ["WCB_1_N", ["WCB_2"], false],
  ["wcb_2", ["WCB_2"], false],
  ["WCB", ["WCB_2"], true],
];

const DEFAULTS: [string, string[], boolean, string | null][] = [
  ["snare_wcb_2_v2", ["WCB_2"], false, "WCB_2"],
  ["snare_wcb_1_uf", ["WCB_1"], false, "WCB_1"],
  ["RALLYCAPS_WCB_2_NW", ["WCB_2"], true, "WCB_2_NW"],
  ["rallycaps_WCB_2_SE_v2", ["WCB_2"], true, "WCB_2_SE"],
  ["brotime_wcb_2_west", ["WCB_2"], true, "WCB_2_west"],
  ["toucan_bs3c", ["BS3_C"], true, null],
  ["castaway_wca", ["WCA_1", "WCA_2"], false, "WCA"],
  ["theCan_bs2", ["BS2_S", "BS2_C"], false, "BS2"],
  ["anything", [], false, null],
];

describe("zoneNameError", () => {
  it.each(GOLDEN)("%s on %j -> ok=%s", (name, benches, ok) => {
    expect(zoneNameError(name, benches) === null).toBe(ok);
  });
});

describe("defaultZoneName", () => {
  it.each(DEFAULTS)("%s on %j shared=%s -> %s", (curve, benches, shared, want) => {
    expect(defaultZoneName(curve, benches, shared)).toBe(want);
  });
});
