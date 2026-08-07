// AOI attribution rules — the lessons from the first bro_time export
// (lasso→inspect→add dropped the polygon; re-lasso logged nothing)
// pinned as tests so they can't regress.

import { describe, expect, it } from "vitest";

import type { LastDraw, SelectionEvent } from "../api/types";
import {
  type CohortProvenance,
  MAX_EVENTS_PER_COHORT,
  appendEvents,
  buildStagedEvents,
} from "./cohortProvenance";

const SQUARE = {
  type: "Polygon" as const,
  coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
};

function draw(api10s: string[], at = "2026-08-06T00:00:00Z"): LastDraw {
  return {
    kind: "polygon",
    polygon: SQUARE,
    filters: {} as LastDraw["filters"],
    api10s,
    at,
  };
}

describe("buildStagedEvents", () => {
  it("attributes draw-sourced wells to the polygon, rest to click_add", () => {
    const events = buildStagedEvents({
      staged: ["A", "B", "C"],
      fresh: ["A", "B", "C"],
      lastDraw: draw(["A", "B"]),
      priorEvents: [],
    });
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({
      kind: "polygon",
      api10s: ["A", "B"],
      polygon: SQUARE,
    });
    expect(events[1]).toMatchObject({ kind: "click_add", api10s: ["C"] });
  });

  it("stamps the AOI even when every staged well is already a member", () => {
    // The retrofit path: re-lasso an existing cohort, Add staged adds
    // nothing, but the polygon must still land in the narrative.
    const events = buildStagedEvents({
      staged: ["A", "B"],
      fresh: [], // all already in the cohort
      lastDraw: draw(["A", "B"]),
      priorEvents: [],
    });
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      kind: "polygon",
      api10s: ["A", "B"],
      polygon: SQUARE,
    });
  });

  it("logs one event per physical draw (dedupe on at)", () => {
    const d = draw(["A", "B"]);
    const first = buildStagedEvents({
      staged: ["A", "B"],
      fresh: ["A", "B"],
      lastDraw: d,
      priorEvents: [],
    });
    const second = buildStagedEvents({
      staged: ["A", "B"],
      fresh: [],
      lastDraw: d, // same draw, "Add staged" clicked again
      priorEvents: first,
    });
    expect(second).toHaveLength(0);
  });

  it("a NEW draw over the same wells logs again", () => {
    const first = buildStagedEvents({
      staged: ["A"],
      fresh: ["A"],
      lastDraw: draw(["A"], "2026-08-06T00:00:00Z"),
      priorEvents: [],
    });
    const second = buildStagedEvents({
      staged: ["A"],
      fresh: [],
      lastDraw: draw(["A"], "2026-08-06T00:05:00Z"),
      priorEvents: first,
    });
    expect(second).toHaveLength(1);
  });

  it("does not spam click_adds for re-staged members off-draw", () => {
    const events = buildStagedEvents({
      staged: ["A", "B"],
      fresh: [], // both already members
      lastDraw: null, // pure click flow, no draw
      priorEvents: [],
    });
    expect(events).toHaveLength(0);
  });

  it("no draw → fresh wells become a click_add (gun-barrel one-offs)", () => {
    const events = buildStagedEvents({
      staged: ["A"],
      fresh: ["A"],
      lastDraw: null,
      priorEvents: [],
    });
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ kind: "click_add", api10s: ["A"] });
  });
});

describe("appendEvents", () => {
  it("caps at MAX_EVENTS_PER_COHORT dropping oldest, flags truncated", () => {
    const mk = (i: number): SelectionEvent => ({
      kind: "click_add",
      at: `t${i}`,
      api10s: [`W${i}`],
      polygon: null,
      filters: {},
    });
    let prov: CohortProvenance = { version: 1, events: [] };
    for (let i = 0; i < MAX_EVENTS_PER_COHORT + 5; i++) {
      prov = appendEvents(prov, [mk(i)]);
    }
    expect(prov.events).toHaveLength(MAX_EVENTS_PER_COHORT);
    expect(prov.truncated).toBe(true);
    expect(prov.events[0]!.at).toBe("t5"); // oldest five dropped
  });

  it("no-op append keeps the object untouched", () => {
    const prov = { version: 1 as const, events: [] };
    expect(appendEvents(prov, [])).toBe(prov);
  });
});
