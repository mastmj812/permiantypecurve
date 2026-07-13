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
