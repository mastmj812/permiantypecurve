// Drop-modal scenario filter (RIP Harambe 2026-10-29: the superseded
// dsu_49_50_5 selection was buried at the bottom of the full list).

import { describe, expect, it } from "vitest";

import { scenarioMatches, searchTerms } from "./scenarioSearch";

const rip = {
  deal_id: "rip_harambe_dsu_49_50_5",
  scenario_id: "plan_rip_harambe_dsu_49_50_5",
  name: null,
};
const gator = {
  deal_id: "gator_tails_sec_44",
  scenario_id: "plan_gator_tails_sec_44_u_turn_dsu_2_11",
  name: "gator_tails_sec_44_u_turn_dsu_2_11",
};

describe("searchTerms", () => {
  it("lowercases, strips punctuation, splits on whitespace", () => {
    expect(searchTerms("  RIP_Harambe  49-50 ")).toEqual(["ripharambe", "4950"]);
  });

  it("empty or punctuation-only query is no filter", () => {
    expect(searchTerms("")).toEqual([]);
    expect(searchTerms(" _ - ")).toEqual([]);
  });
});

describe("scenarioMatches", () => {
  it("matches across underscores (the typed-without-separator case)", () => {
    expect(scenarioMatches(rip, searchTerms("ripharambe"))).toBe(true);
    expect(scenarioMatches(rip, searchTerms("rip harambe"))).toBe(true);
    expect(scenarioMatches(gator, searchTerms("ripharambe"))).toBe(false);
  });

  it("requires every term, each against name, deal_id or scenario_id", () => {
    expect(scenarioMatches(rip, searchTerms("harambe 49_50"))).toBe(true);
    expect(scenarioMatches(rip, searchTerms("harambe 15_22"))).toBe(false);
    expect(scenarioMatches(gator, searchTerms("uturn 2_11"))).toBe(true);
  });

  it("falls back to ids when the scenario has no name (phantom rows)", () => {
    expect(scenarioMatches(rip, searchTerms("plan_rip"))).toBe(true);
  });

  it("no terms matches everything", () => {
    expect(scenarioMatches(gator, [])).toBe(true);
  });
});
