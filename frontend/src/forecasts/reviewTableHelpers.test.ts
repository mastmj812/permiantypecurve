// PPF derivation + the review-table TSV clipboard dump. The TSV is
// what lands in Excel on paste, so the shape (header row, tab cells,
// CRLF lines, plain numbers) is contract, not cosmetics.

import { describe, expect, it } from "vitest";

import type { ForecastRow } from "../api/forecasts";
import { buildReviewTsv, ppfOf } from "./reviewTableHelpers";

function row(overrides: Partial<ForecastRow>): ForecastRow {
  return {
    id: "f-1",
    api10: "4200000001",
    stream: "oil",
    model_type: "modified_hyperbolic",
    params: {},
    qi: null,
    di_initial: null,
    di_effective: null,
    b: null,
    df_terminal: null,
    qo: null,
    peak_index_months: null,
    eur: null,
    peak_month_date: null,
    peak_rate: null,
    fit_method: "rate_cum",
    fit_r2: null,
    fit_rmse: null,
    fit_at_bound: false,
    bound_note: null,
    downtime_ratio: null,
    diagnostics: null,
    manual_override: false,
    locked: false,
    updated_at: "2026-01-01T00:00:00Z",
    well_name: null,
    well_operator: null,
    well_formation: null,
    well_lateral_ft: null,
    well_proppant_lbs: null,
    well_vintage_year: null,
    well_first_prod_date: null,
    well_county: null,
    well_novi_oil_eur: null,
    well_water_source: null,
    well_wor_cv: null,
    actual_cum: null,
    eur_remaining: null,
    eur_displayed: null,
    ...overrides,
  };
}

describe("ppfOf", () => {
  it("divides proppant by lateral", () => {
    expect(
      ppfOf(row({ well_proppant_lbs: 24_000_000, well_lateral_ft: 10_000 })),
    ).toBe(2400);
  });

  it("is null when either input is missing or non-positive", () => {
    expect(ppfOf(row({ well_proppant_lbs: null, well_lateral_ft: 10_000 }))).toBeNull();
    expect(ppfOf(row({ well_proppant_lbs: 24e6, well_lateral_ft: null }))).toBeNull();
    expect(ppfOf(row({ well_proppant_lbs: 24e6, well_lateral_ft: 0 }))).toBeNull();
  });
});

describe("buildReviewTsv", () => {
  const ctx = {
    fits: new Map(),
    excluded: new Set<string>(),
    outliers: new Set<string>(),
    pendingTransfer: new Set<string>(),
    exclusionReasons: new Map<string, { code: string; note: string }>(),
  };

  it("emits a header plus one CRLF-joined line per row with tab cells", () => {
    const tsv = buildReviewTsv(
      [
        row({
          api10: "4200000001",
          well_name: "TEST 1H",
          well_lateral_ft: 10_000,
          well_proppant_lbs: 24_000_000,
          eur: 500_000,
          fit_r2: 0.9876,
        }),
      ],
      ctx,
    );
    const lines = tsv.split("\r\n");
    expect(lines).toHaveLength(2);
    expect(lines[0]!.split("\t")[0]).toBe("included");
    const cells = lines[1]!.split("\t");
    expect(cells[0]).toBe("TRUE");
    expect(cells[1]).toBe("4200000001");
    expect(cells[2]).toBe("TEST 1H");
    // Plain unformatted numbers so Excel parses them as numerics.
    expect(cells[6]).toBe("10000");
    expect(cells[7]).toBe("2400");
    expect(cells[8]).toBe("500000");
    expect(cells[11]).toBe("0.988");
  });

  it("sanitizes tabs/newlines in text and carries exclusion reasons into flags", () => {
    const tsv = buildReviewTsv(
      [row({ api10: "4200000002", well_name: "BAD\tNAME\n2H" })],
      {
        ...ctx,
        excluded: new Set(["4200000002"]),
        exclusionReasons: new Map([
          ["4200000002", { code: "frac_hit", note: "offset frac" }],
        ]),
      },
    );
    const cells = tsv.split("\r\n")[1]!.split("\t");
    expect(cells[0]).toBe("FALSE");
    expect(cells[2]).toBe("BAD NAME 2H");
    expect(cells[16]).toBe("excluded: frac_hit (offset frac)");
    // Row stays rectangular despite the hostile name.
    expect(cells).toHaveLength(17);
  });

  it("leaves missing numerics as empty cells, not dashes", () => {
    const tsv = buildReviewTsv([row({ api10: "4200000003" })], ctx);
    const cells = tsv.split("\r\n")[1]!.split("\t");
    expect(cells[6]).toBe(""); // lateral
    expect(cells[7]).toBe(""); // ppf
    expect(cells[8]).toBe(""); // eur
  });
});
