import { describe, expect, it } from "vitest";

import { DEFAULT_FILTER_SPEC, SCENARIO_COLORS } from "../api/types";
import { filterSpecToQuery } from "../api/wells";
import { wellsColorExpr } from "./wellsLayers";

describe("development-scenario query params", () => {
  it("adds nothing to the default tile URL", () => {
    const q = new URLSearchParams(filterSpecToQuery(DEFAULT_FILTER_SPEC));
    for (const k of [
      "scenario_classes",
      "scenario_benches",
      "parent_benches",
      "parent_side",
      "parent_dtvd_max_ft",
      "parent_age_min_days",
      "parent_age_max_days",
    ]) {
      expect(q.has(k)).toBe(false);
    }
  });

  it("drops parent side / dTVD cap unless a parent bench is named", () => {
    const q = new URLSearchParams(
      filterSpecToQuery({
        ...DEFAULT_FILTER_SPEC,
        parent_side: "above",
        parent_dtvd_max_ft: 700,
      }),
    );
    expect(q.has("parent_side")).toBe(false);
    expect(q.has("parent_dtvd_max_ft")).toBe(false);
  });

  it("serializes a bench-pair query (WCA_1 beneath LSSH, parents >= 2 yr)", () => {
    const q = new URLSearchParams(
      filterSpecToQuery({
        ...DEFAULT_FILTER_SPEC,
        scenario_classes: ["underfill", "no_data"],
        parent_benches: ["LSSH"],
        parent_side: "above",
        parent_dtvd_max_ft: 900,
        parent_age_min_days: 730,
      }),
    );
    expect(q.get("scenario_classes")).toBe("underfill,no_data");
    expect(q.get("parent_benches")).toBe("LSSH");
    expect(q.get("parent_side")).toBe("above");
    expect(q.get("parent_dtvd_max_ft")).toBe("900");
    expect(q.get("parent_age_min_days")).toBe("730");
    // "any" is the server default — never sent
    const any = new URLSearchParams(
      filterSpecToQuery({ ...DEFAULT_FILTER_SPEC, parent_benches: ["LSSH"] }),
    );
    expect(any.has("parent_side")).toBe(false);
  });
});

describe("scenario color expression", () => {
  it("coalesces the (MVT-omitted) null class to no_data and keeps selection on top", () => {
    const expr = wellsColorExpr("scenario") as unknown[];
    expect(expr[0]).toBe("case");
    expect(expr[2]).toBe("#facc15"); // selected halo wins
    const match = expr[3] as unknown[];
    expect(match[0]).toBe("match");
    expect(match[1]).toEqual(["coalesce", ["get", "scenario_class"], "no_data"]);
    expect(match[match.length - 1]).toBe(SCENARIO_COLORS.no_data);
    // no zoom expression nested anywhere (it must be outermost if used)
    expect(JSON.stringify(expr)).not.toContain('"zoom"');
  });

  it("formation mode is unchanged", () => {
    const expr = wellsColorExpr("formation") as unknown[];
    expect(JSON.stringify(expr)).not.toContain("scenario_class");
  });
});
