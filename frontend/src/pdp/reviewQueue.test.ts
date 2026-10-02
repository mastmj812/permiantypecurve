import { describe, expect, it } from "vitest";

import type { PdpRow, PdpStream } from "../api/pdp";
import {
  cleanUnsigned,
  groupWells,
  nextUnsigned,
  orderQueue,
  stepQueue,
  wellStatus,
} from "./reviewQueue";

function row(
  api10: string,
  name: string,
  stream: PdpStream,
  over: Partial<PdpRow> = {},
): PdpRow {
  return {
    api10,
    well_name: name,
    formation_blueox: "WCA_1",
    stream,
    method: "daily_fit",
    qi: 100,
    Di: 1,
    b: 1,
    Df: 0.08,
    anchor_date: "2023-01-01",
    fit_start_date: null,
    data_through: "2026-07-03",
    uptime_factor: 1,
    uptime_basis: { factor: 1, event_days: 0, routine_down_days: 0 },
    fit_r2_log: 0.9,
    n_points: 100,
    tail_ratio: 1,
    cum_to_date: 1,
    remaining: 1,
    eur: 2,
    review_flags: [],
    breaks: [],
    diagnostics: {},
    manual_override: false,
    locked: false,
    ...over,
  };
}

function well(
  api10: string,
  name: string,
  over: Partial<PdpRow> = {},
  perStream: Partial<Record<PdpStream, Partial<PdpRow>>> = {},
) {
  return (["oil", "gas", "water"] as PdpStream[]).map((s) =>
    row(api10, name, s, { ...over, ...(perStream[s] ?? {}) }),
  );
}

const rows = [
  ...well("1", "Lowe 61H", { review_flags: ["at_bound"] }), // clean
  ...well("2", "Sheridan 73H", {}, { oil: { review_flags: ["recent_break"] } }), // flagged
  ...well("3", "Atlanta 2H", { method: "shut_in", review_flags: ["shut_in"] }), // shut-in
  ...well("4", "Atlanta 72H", { locked: true }), // signed
  ...well("5", "Lowe 63H", {}, { gas: { locked: true } }), // partial, clean
];
const queue = orderQueue(groupWells(rows));

describe("PDP review queue", () => {
  it("orders shut-in, flagged, clean, then signed", () => {
    expect(queue.map((w) => w.name)).toEqual([
      "Atlanta 2H",
      "Sheridan 73H",
      "Lowe 61H",
      "Lowe 63H",
      "Atlanta 72H",
    ]);
  });

  it("derives well status from stream locks", () => {
    const by = Object.fromEntries(queue.map((w) => [w.api10, wellStatus(w)]));
    expect(by).toEqual({
      "1": "open",
      "2": "open",
      "3": "open",
      "4": "signed",
      "5": "partial",
    });
  });

  it("next unsigned skips signed wells and wraps", () => {
    expect(nextUnsigned(queue, "5")).toBe("3"); // wraps past signed 72H
    expect(nextUnsigned(queue, "3")).toBe("2");
    expect(nextUnsigned(queue, null)).toBe("3");
    const allSigned = orderQueue(groupWells(well("9", "x", { locked: true })));
    expect(nextUnsigned(allSigned, "9")).toBeNull();
  });

  it("steps through the queue in both directions", () => {
    expect(stepQueue(queue, "3", 1)).toBe("2");
    expect(stepQueue(queue, "3", -1)).toBe("4");
    expect(stepQueue(queue, null, 1)).toBe("3");
  });

  it("bulk-accept set is the unsigned clean wells only", () => {
    expect(cleanUnsigned(queue).map((w) => w.name)).toEqual([
      "Lowe 61H",
      "Lowe 63H",
    ]);
  });
});
