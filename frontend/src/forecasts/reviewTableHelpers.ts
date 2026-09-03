// Pure (non-component) helpers for per-well forecast tables: decline
// helpers, format helpers, a tiny median helper, and a well/stream
// index. Split out of reviewTable.tsx so that module can export only
// components — react-refresh/only-export-components requires a file to
// export components (or constants) exclusively for Fast Refresh to work.

import {
  effectiveDecline,
  type ForecastRow,
  type Stream,
} from "../api/forecasts";

/**
 * Effective 1-yr decline for a stream's fit. Prefers the server-
 * emitted ``di_effective``; falls back to recomputing from raw params
 * so wells whose forecast persisted before that column was populated
 * still show a value.
 */
export function effDiFor(row: ForecastRow | undefined | null): number | null {
  if (!row) return null;
  if (row.di_effective != null && Number.isFinite(row.di_effective)) {
    return row.di_effective;
  }
  const Di = row.params?.Di;
  const b = row.params?.b;
  if (Di == null || !Number.isFinite(Di)) return null;
  return effectiveDecline(Di, b ?? null);
}

/**
 * Generic decline-style helper that also accepts the workspace
 * payload shape (override JSONB) so the TC workspace can compute Di
 * for both override and global rows uniformly. The payload's
 * ``di_initial`` / ``params.Di`` keys mirror ForecastRow so the same
 * fallback logic applies.
 */
export function effDiFromPayload(
  payload: Record<string, unknown> | null | undefined,
): number | null {
  if (!payload) return null;
  // Workspace payloads carry the server-emitted di_effective ONLY
  // when the payload is a global forecast snapshot — overrides may
  // skip it. Recompute from params when missing.
  const params = (payload.params as Record<string, unknown> | null) ?? null;
  const di = params?.Di ?? payload.di_initial;
  const b = params?.b ?? payload.b;
  if (di == null || !Number.isFinite(di)) return null;
  return effectiveDecline(di as number, (b as number | null | undefined) ?? null);
}

export function fmtDi(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${Math.round(v * 100)}%`;
}

export function fmtInt(v: number | null | undefined): string {
  if (v == null) return "—";
  return Math.round(v).toLocaleString();
}

export function fmtVol(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return v.toFixed(0);
}

/** PPF (proppant lb per lateral ft) for a review row — same convention
 * as the export builder (well_rows.py). Null when either input is
 * missing or non-positive. */
export function ppfOf(row: ForecastRow): number | null {
  const prop = row.well_proppant_lbs;
  const lat = row.well_lateral_ft;
  if (prop == null || lat == null || prop <= 0 || lat <= 0) return null;
  return prop / lat;
}

export interface ReviewTsvContext {
  fits: Map<string, Map<Stream, ForecastRow>>;
  excluded: Set<string>;
  outliers: Set<string>;
  pendingTransfer: Set<string>;
  exclusionReasons: Map<string, { code: string; note: string }>;
}

// One cell: strip the characters that would break TSV structure.
function tsvCell(v: string): string {
  return v.replace(/[\t\r\n]+/g, " ").trim();
}

function num(v: number | null | undefined, decimals: number): string {
  if (v == null || !Number.isFinite(v)) return "";
  return v.toFixed(decimals);
}

/**
 * Tab-separated dump of the review table for clipboard → Excel paste.
 * Mirrors the visible columns (plus a PPF and flags column); rows come
 * in already filtered + sorted so the paste matches what's on screen.
 * Numbers are plain (no thousands separators) so Excel parses them as
 * numerics; Di columns are 1-yr effective FRACTIONS (format as % in
 * Excel), matching how the on-screen percentages are derived.
 */
export function buildReviewTsv(rows: ForecastRow[], ctx: ReviewTsvContext): string {
  const header = [
    "included",
    "api10",
    "well name",
    "formation",
    "operator",
    "first prod date",
    "lateral (ft)",
    "PPF (lb/ft)",
    "EUR (bbl)",
    "Novi oil EUR (bbl)",
    "EUR/ft (bbl/ft)",
    "R2",
    "oil Di (1-yr eff)",
    "gas Di (1-yr eff)",
    "water Di (1-yr eff)",
    "downtime ratio",
    "flags",
  ];
  const lines = [header.join("\t")];
  for (const r of rows) {
    const perStream = ctx.fits.get(r.api10);
    const eurPerFt =
      r.eur != null && r.well_lateral_ft ? r.eur / r.well_lateral_ft : null;
    const flags: string[] = [];
    if (ctx.pendingTransfer.has(r.api10)) flags.push("pending transfer");
    if (ctx.outliers.has(r.api10)) flags.push("outlier");
    if (r.fit_at_bound) flags.push("at bound");
    if (r.downtime_ratio != null && r.downtime_ratio > 0.15) flags.push("downtime");
    if (r.manual_override) flags.push("edited");
    if (ctx.excluded.has(r.api10)) {
      const reason = ctx.exclusionReasons.get(r.api10);
      flags.push(
        reason
          ? `excluded: ${reason.code}${reason.note ? ` (${reason.note})` : ""}`
          : "excluded",
      );
    }
    lines.push(
      [
        ctx.excluded.has(r.api10) ? "FALSE" : "TRUE",
        tsvCell(r.api10),
        tsvCell(r.well_name ?? ""),
        tsvCell(r.well_formation ?? ""),
        tsvCell(r.well_operator ?? ""),
        tsvCell(r.well_first_prod_date ?? ""),
        num(r.well_lateral_ft, 0),
        num(ppfOf(r), 0),
        num(r.eur, 0),
        num(r.well_novi_oil_eur, 0),
        num(eurPerFt, 2),
        num(r.fit_r2, 3),
        num(effDiFor(perStream?.get("oil")), 4),
        num(effDiFor(perStream?.get("gas")), 4),
        num(effDiFor(perStream?.get("water")), 4),
        num(r.downtime_ratio, 3),
        tsvCell(flags.join("; ")),
      ].join("\t"),
    );
  }
  return lines.join("\r\n");
}

export function median(vals: number[]): number | null {
  if (vals.length === 0) return null;
  const s = [...vals].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

/**
 * Build a per-(api10, stream) lookup from a flat ForecastRow list.
 * Used by the Review tab to surface gas/water decline alongside oil
 * without a second fetch.
 */
export function indexFitsByWellStream(
  rows: ForecastRow[],
): Map<string, Map<Stream, ForecastRow>> {
  const m = new Map<string, Map<Stream, ForecastRow>>();
  for (const f of rows) {
    let per = m.get(f.api10);
    if (!per) {
      per = new Map<Stream, ForecastRow>();
      m.set(f.api10, per);
    }
    per.set(f.stream, f);
  }
  return m;
}
