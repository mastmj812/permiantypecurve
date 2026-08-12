import { describe, expect, it } from "vitest";

import type { NarviDealSticks, NarviDealStickWell } from "../api/narvi";
import { OTHER_COLOR, colorForFormation } from "./formations";
import {
  UNSET_BENCH,
  benchKey,
  benchKeysFor,
  buildNarviStickFeatures,
  narviBenchFilter,
} from "./narviSticks";

function well(overrides: Partial<NarviDealStickWell>): NarviDealStickWell {
  return {
    deal_id: "vault_dsu_1_11",
    scenario_id: "plan_a",
    scenario_name: "Plan A",
    well_name: "W 1H",
    formation: "WCA_1",
    category: "PUD",
    well_type: "single",
    legs_geojson: '{"type":"MultiLineString","coordinates":[[[0,0],[1,1]]]}',
    turn_geojson: null,
    ...overrides,
  };
}

function sticks(wells: NarviDealStickWell[]): NarviDealSticks {
  return {
    deal_ids: [...new Set(wells.map((w) => w.deal_id))],
    missing_deal_ids: [],
    wells,
  };
}

describe("benchKey", () => {
  it("strips the bimodal _b landing-target suffix", () => {
    expect(benchKey("WCA_1_b")).toBe("WCA_1");
    expect(benchKey("WCA_1")).toBe("WCA_1");
  });

  it("maps a missing formation to the unset bucket", () => {
    expect(benchKey(null)).toBe(UNSET_BENCH);
    expect(benchKey(undefined)).toBe(UNSET_BENCH);
    expect(benchKey("")).toBe(UNSET_BENCH);
  });
});

describe("buildNarviStickFeatures", () => {
  it("emits one legs feature per well, colored by the stripped bench", () => {
    const fc = buildNarviStickFeatures(sticks([well({ formation: "WCA_1_b" })]));
    expect(fc.features).toHaveLength(1);
    const props = fc.features[0]!.properties as Record<string, unknown>;
    expect(props.formation).toBe("WCA_1_b"); // raw preserved for display
    expect(props.formation_key).toBe("WCA_1"); // toggle/filter key stripped
    expect(props.color).toBe(colorForFormation("WCA_1")); // NOT gray
    expect(props.color).not.toBe(OTHER_COLOR);
    expect(props.kind).toBe("legs");
  });

  it("adds a turn feature sharing the well's bench key so U-turn arcs hide with their bench", () => {
    const fc = buildNarviStickFeatures(
      sticks([
        well({
          well_type: "uturn",
          turn_geojson: '{"type":"LineString","coordinates":[[0,0],[1,1]]}',
        }),
      ]),
    );
    expect(fc.features).toHaveLength(2);
    const kinds = fc.features.map((f) => (f.properties as { kind: string }).kind);
    expect(kinds).toEqual(["legs", "turn"]);
    const keys = new Set(
      fc.features.map((f) => (f.properties as { formation_key: string }).formation_key),
    );
    expect(keys).toEqual(new Set(["WCA_1"]));
  });

  it("skips null and malformed geometry instead of throwing", () => {
    const fc = buildNarviStickFeatures(
      sticks([
        well({ legs_geojson: null }),
        well({ well_name: "W 2H", legs_geojson: "not json" }),
        well({ well_name: "W 3H" }),
      ]),
    );
    expect(fc.features).toHaveLength(1);
    expect((fc.features[0]!.properties as { well_name: string }).well_name).toBe("W 3H");
  });

  it("buckets a NULL formation as unset with the fallback color", () => {
    const fc = buildNarviStickFeatures(sticks([well({ formation: null })]));
    const props = fc.features[0]!.properties as Record<string, unknown>;
    expect(props.formation_key).toBe(UNSET_BENCH);
    expect(props.color).toBe(OTHER_COLOR);
  });
});

describe("benchKeysFor", () => {
  it("dedupes _b variants into their base bench, stratigraphic order, unset last", () => {
    const keys = benchKeysFor(
      sticks([
        well({ formation: "WCA_1" }),
        well({ formation: "WCA_1_b" }),
        well({ formation: "BS3_C" }),
        well({ formation: null }),
        well({ formation: "WDFD" }),
      ]),
    );
    // CODE_DISPLAY_ORDER: BS3_C (Bone Spring) < WCA_1 (Wolfcamp) < WDFD (Other)
    expect(keys).toEqual(["BS3_C", "WCA_1", "WDFD", UNSET_BENCH]);
  });

  it("spans every selected deal — one toggle per bench, not per DSU", () => {
    const keys = benchKeysFor(
      sticks([
        well({ deal_id: "vault_dsu_1_11", formation: "WCA_1" }),
        well({ deal_id: "vault_dsu_14_23", formation: "WCA_1" }),
        well({ deal_id: "vault_dsu_14_23", formation: "BS3_C" }),
      ]),
    );
    expect(keys).toEqual(["BS3_C", "WCA_1"]); // deduped across deals
  });
});

describe("narviBenchFilter", () => {
  const keys = ["BS3_C", "WCA_1", UNSET_BENCH];

  it("shows every bench by default (missing key = visible)", () => {
    expect(narviBenchFilter(keys, {}) as unknown).toEqual([
      "in",
      ["get", "formation_key"],
      ["literal", keys],
    ]);
  });

  it("drops only benches explicitly toggled off — the NULL bucket stays visible", () => {
    const filter = narviBenchFilter(keys, { WCA_1: false }) as unknown;
    expect(filter).toEqual([
      "in",
      ["get", "formation_key"],
      ["literal", ["BS3_C", UNSET_BENCH]],
    ]);
  });
});
