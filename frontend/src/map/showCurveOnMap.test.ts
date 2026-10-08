import { beforeEach, describe, expect, it } from "vitest";

import type { WellDetailLite } from "../api/wells";
import { useCohortStore } from "../store/cohortStore";
import { activateCurveCohort, curveFormations, wellBounds } from "./showCurveOnMap";

const well = (sh: [number, number] | null, bh: [number, number] | null) =>
  ({
    sh_lon: sh?.[0] ?? null, sh_lat: sh?.[1] ?? null,
    bh_lon: bh?.[0] ?? null, bh_lat: bh?.[1] ?? null,
  }) as unknown as WellDetailLite;

describe("curveFormations", () => {
  it("prefers the formations recorded at draw time, then the saved filter", () => {
    expect(curveFormations({ name: "x", included_api10s: [], provenance: { formations: ["WCB_2"] },
      filter_spec: { formations: ["WCA_1"] } })).toEqual(["WCB_2"]);
    expect(curveFormations({ name: "x", included_api10s: [], filter_spec: { formations: ["WCA_1"] } }))
      .toEqual(["WCA_1"]);
    expect(curveFormations({ name: "x", included_api10s: [] })).toEqual([]);
  });
});

describe("wellBounds", () => {
  it("spans surface AND bottom-hole locations so whole laterals are in view", () => {
    expect(wellBounds([well([-103.6, 31.98], [-103.6, 32.0]), well([-103.5, 31.9], null)]))
      .toEqual([-103.6, 31.9, -103.5, 32.0]);
  });
  it("is null when no well has a location", () => {
    expect(wellBounds([well(null, null)])).toBeNull();
  });
});

describe("activateCurveCohort", () => {
  beforeEach(() => useCohortStore.setState({ cohorts: [], activeCohortId: null }));

  it("creates the cohort once, then reuses and re-syncs it on repeat clicks", () => {
    const tc = { name: "rallycaps_WCB_2_SE", included_api10s: ["4247500001", "4247500002"] };
    const id = activateCurveCohort(tc);
    expect(useCohortStore.getState().activeCohortId).toBe(id);
    expect(useCohortStore.getState().cohorts[0]?.api10s).toEqual(["4247500001", "4247500002"]);

    useCohortStore.getState().setActive(null);
    const again = activateCurveCohort({ ...tc, included_api10s: ["4247500001"] }); // a well culled in anduin
    expect(again).toBe(id);
    expect(useCohortStore.getState().cohorts).toHaveLength(1);
    expect(useCohortStore.getState().cohorts[0]?.api10s).toEqual(["4247500001"]);
    expect(useCohortStore.getState().activeCohortId).toBe(id);
  });
});
