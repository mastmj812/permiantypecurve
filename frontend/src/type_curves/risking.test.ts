// TS mirror of backend tests/test_risking.py — the display-time
// scaling must agree with the export-time scaling or the UI and the
// delivered files diverge.

import { describe, expect, it } from "vitest";

import type { StreamSeries } from "../api/typeCurves";
import {
  isRisked,
  normalizeMultipliers,
  riskedNameSuffix,
  riskingLabel,
  riskStreamSeries,
  scaleRates,
} from "./risking";

function series(): StreamSeries {
  return {
    p10: [400, 380, null],
    p25: [350, 330, null],
    p50: [300, 280, null],
    p75: [250, 230, null],
    p90: [200, 180, null],
    mean: [305, 285, null],
    well_count: [12, 12, 11],
    implied_eur_per_1000ft: { p50: 150_000, mean: 155_000 },
    fitted: {
      model_type: "ramp_arps",
      qi: 300,
      Di: 2.5,
      b: 1.1,
      Df: 0.08,
      eur_per_unit: 200_000,
      r2: 0.97,
      smoothed_rate: [120, 240, 300, 270],
      qo: 120,
      peak_index: 3,
      ramp_eur: 20_000,
      arps_eur: 180_000,
    },
    fitted_eur_per_unit: { p10: 260_000, p50: 200_000, p90: null },
    fitted_per_percentile: {
      p50: {
        model_type: "ramp_arps",
        qi: 300,
        Di: 2.5,
        b: 1.1,
        Df: 0.08,
        eur_per_unit: 200_000,
        smoothed_rate: [],
        qo: 120,
        peak_index: 3,
      },
      p90: null,
    },
  };
}

describe("normalizeMultipliers / isRisked / riskingLabel", () => {
  it("defaults absent and invalid values to 1.0", () => {
    expect(normalizeMultipliers(undefined)).toEqual({ oil: 1, gas: 1, water: 1 });
    expect(normalizeMultipliers({ oil: 0.85 })).toEqual({ oil: 0.85, gas: 1, water: 1 });
    expect(normalizeMultipliers({ oil: -2, gas: Number.NaN })).toEqual({
      oil: 1,
      gas: 1,
      water: 1,
    });
  });

  it("flags risked only on a non-1.0 multiplier", () => {
    expect(isRisked({})).toBe(false);
    expect(isRisked({ water: 1.0 })).toBe(false);
    expect(isRisked({ water: 0.9 })).toBe(true);
  });

  it("labels only the non-1.0 streams", () => {
    expect(riskingLabel({ oil: 0.85, gas: 0.9 })).toBe("×0.85 oil · ×0.90 gas");
    expect(riskingLabel({})).toBe("");
  });

  // Mirrors backend risking.risked_name_suffix (byte-for-byte contract
  // with the PPTX param table).
  it("name suffix collapses a uniform factor, lists mixed ones", () => {
    expect(riskedNameSuffix({})).toBe("");
    expect(riskedNameSuffix({ oil: 0.8, gas: 0.8, water: 0.8 })).toBe(
      " [RISKED ×0.80]",
    );
    expect(riskedNameSuffix({ oil: 0.5 })).toBe(" [RISKED ×0.50 oil]");
    expect(riskedNameSuffix({ oil: 0.85, gas: 0.9 })).toBe(
      " [RISKED ×0.85 oil · ×0.90 gas]",
    );
  });
});

describe("riskStreamSeries", () => {
  it("is identity (same object) at mul 1.0", () => {
    const s = series();
    expect(riskStreamSeries(s, 1.0)).toBe(s);
    expect(scaleRates(s.p50, 1.0)).toBe(s.p50);
  });

  it("scales rate/EUR values, preserves nulls, shape params, counts", () => {
    const s = series();
    const out = riskStreamSeries(s, 0.85);
    expect(out).not.toBe(s);
    expect(out.p50).toEqual([255, 238, null]);
    expect(out.fitted?.qi).toBeCloseTo(255);
    expect(out.fitted?.qo).toBeCloseTo(102);
    expect(out.fitted?.eur_per_unit).toBeCloseTo(170_000);
    expect(out.fitted?.smoothed_rate).toEqual([102, 204, 255, 229.5]);
    // shape params untouched
    expect(out.fitted?.Di).toBe(2.5);
    expect(out.fitted?.b).toBe(1.1);
    expect(out.fitted?.Df).toBe(0.08);
    expect(out.fitted?.peak_index).toBe(3);
    expect(out.fitted?.r2).toBe(0.97);
    expect(out.well_count).toEqual([12, 12, 11]);
    // per-percentile stores move together; null slots survive
    expect(out.fitted_per_percentile?.p50?.qi).toBeCloseTo(255);
    expect(out.fitted_per_percentile?.p90).toBeNull();
    expect(out.fitted_eur_per_unit?.p10).toBeCloseTo(221_000);
    expect(out.fitted_eur_per_unit?.p90).toBeNull();
    // input untouched
    expect(s.p50).toEqual([300, 280, null]);
    expect(s.fitted?.qi).toBe(300);
  });

  it("preserves SPE order (P10 >= P50 >= P90) under any positive mul", () => {
    const out = riskStreamSeries(series(), 0.7);
    for (const i of [0, 1]) {
      expect(out.p10[i]!).toBeGreaterThanOrEqual(out.p50[i]!);
      expect(out.p50[i]!).toBeGreaterThanOrEqual(out.p90[i]!);
    }
  });
});
