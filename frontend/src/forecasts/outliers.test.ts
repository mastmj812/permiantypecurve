import { describe, expect, it } from "vitest";

import { computeOutliers, type OutlierInput } from "./outliers";

const ROW = (api10: string, eur: number | null, lateral_ft: number | null): OutlierInput => ({
  api10,
  eur,
  lateral_ft,
});

describe("computeOutliers", () => {
  it("returns no outliers and null stats on empty input", () => {
    const r = computeOutliers([]);
    expect(r.outlierApi10s.size).toBe(0);
    expect(r.stats).toBeNull();
  });

  it("returns no outliers with fewer than 3 usable rows", () => {
    const r = computeOutliers([
      ROW("a", 100_000, 10_000),
      ROW("b", 200_000, 10_000),
    ]);
    expect(r.outlierApi10s.size).toBe(0);
    expect(r.stats).toBeNull();
  });

  it("skips rows missing EUR or lateral_ft and marks them as non-outliers", () => {
    const r = computeOutliers([
      ROW("a", null, 10_000),
      ROW("b", 500_000, null),
      ROW("c", 200_000, 0),
      ROW("d", 300_000, 10_000),
      ROW("e", 400_000, 10_000),
      ROW("f", 500_000, 10_000),
    ]);
    expect(r.perWell["a"]!.eurPerFt).toBeNull();
    expect(r.perWell["a"]!.isOutlier).toBe(false);
    expect(r.perWell["b"]!.isOutlier).toBe(false);
    expect(r.perWell["c"]!.isOutlier).toBe(false);
    // d-f have eurPerFt 30/40/50 — within 2σ of median 40.
    expect(r.outlierApi10s.size).toBe(0);
  });

  it("flags a high outlier 3σ above median", () => {
    // Wells a-e have EUR/ft = 30, e is the outlier at 130.
    const rows: OutlierInput[] = [
      ROW("a", 250_000, 10_000),
      ROW("b", 260_000, 10_000),
      ROW("c", 270_000, 10_000),
      ROW("d", 280_000, 10_000),
      ROW("e", 1_300_000, 10_000),
    ];
    const r = computeOutliers(rows);
    expect(r.outlierApi10s.has("e")).toBe(true);
    expect(r.outlierApi10s.has("a")).toBe(false);
    expect(r.outlierApi10s.has("d")).toBe(false);
  });

  it("flags a low outlier 3σ below median", () => {
    const rows: OutlierInput[] = [
      ROW("a", 250_000, 10_000),
      ROW("b", 260_000, 10_000),
      ROW("c", 270_000, 10_000),
      ROW("d", 280_000, 10_000),
      ROW("e", 5_000, 10_000), // 0.5 EUR/ft vs median 26
    ];
    const r = computeOutliers(rows);
    expect(r.outlierApi10s.has("e")).toBe(true);
  });

  it("computes median and thresholds correctly", () => {
    const rows: OutlierInput[] = [
      ROW("a", 100_000, 10_000), // 10
      ROW("b", 200_000, 10_000), // 20
      ROW("c", 300_000, 10_000), // 30
      ROW("d", 400_000, 10_000), // 40
      ROW("e", 500_000, 10_000), // 50
    ];
    const r = computeOutliers(rows);
    expect(r.stats!.median).toBe(30);
    expect(r.stats!.mean).toBe(30);
    // population variance: ((-20)^2+(-10)^2+0+10^2+20^2)/5 = 200; σ ≈ 14.142
    expect(r.stats!.stddev).toBeCloseTo(Math.sqrt(200), 6);
    expect(r.stats!.lowThreshold).toBeCloseTo(30 - 2 * Math.sqrt(200), 6);
    expect(r.stats!.highThreshold).toBeCloseTo(30 + 2 * Math.sqrt(200), 6);
    // 5-row spread: 10..50, all within ±2σ ≈ 28.3 of median 30. No outliers.
    expect(r.outlierApi10s.size).toBe(0);
  });

  it("handles even-count median correctly (avg of middle two)", () => {
    const rows: OutlierInput[] = [
      ROW("a", 100_000, 10_000), // 10
      ROW("b", 200_000, 10_000), // 20
      ROW("c", 300_000, 10_000), // 30
      ROW("d", 400_000, 10_000), // 40
    ];
    const r = computeOutliers(rows);
    expect(r.stats!.median).toBe(25);
  });
});
