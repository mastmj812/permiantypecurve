// PDP review queue — pure helpers (tested in reviewQueue.test.ts).
//
// The unit of review is a WELL (all three streams on one screen). Queue
// order puts the work that needs judgment first:
//   0 shut-in   any stream classified shut_in (PDNP / dead wells)
//   1 flagged   any review flag beyond at_bound
//   2 clean     only at_bound (or nothing) — bulk-acceptable
//   3 signed    every stream locked — drops to the bottom
// ties break by well name.

import type { PdpRow, PdpStream } from "../api/pdp";

export interface QueueWell {
  api10: string;
  name: string;
  bench: string | null;
  byStream: Partial<Record<PdpStream, PdpRow>>;
}

export type WellStatus = "signed" | "partial" | "open";

export function groupWells(rows: PdpRow[]): QueueWell[] {
  const by = new Map<string, QueueWell>();
  for (const r of rows) {
    const g = by.get(r.api10) ?? {
      api10: r.api10,
      name: r.well_name ?? r.api10,
      bench: r.formation_blueox,
      byStream: {},
    };
    g.byStream[r.stream] = r;
    by.set(r.api10, g);
  }
  return [...by.values()];
}

function streamRows(w: QueueWell): PdpRow[] {
  return Object.values(w.byStream).filter((r): r is PdpRow => r !== undefined);
}

export function wellStatus(w: QueueWell): WellStatus {
  const rows = streamRows(w);
  const n = rows.filter((r) => r.locked).length;
  if (rows.length > 0 && n === rows.length) return "signed";
  return n > 0 ? "partial" : "open";
}

export function wellFlags(w: QueueWell): string[] {
  return [...new Set(streamRows(w).flatMap((r) => r.review_flags))].filter(
    (f) => f !== "at_bound",
  );
}

export function wellRank(w: QueueWell): number {
  if (wellStatus(w) === "signed") return 3;
  if (streamRows(w).some((r) => r.method === "shut_in")) return 0;
  return wellFlags(w).length > 0 ? 1 : 2;
}

export function orderQueue(wells: QueueWell[]): QueueWell[] {
  return [...wells].sort(
    (a, b) => wellRank(a) - wellRank(b) || a.name.localeCompare(b.name),
  );
}

/** Next well after ``current`` (wrapping) that is not signed off. */
export function nextUnsigned(
  queue: QueueWell[],
  current: string | null,
): string | null {
  const start =
    current === null ? -1 : queue.findIndex((w) => w.api10 === current);
  for (let k = 1; k <= queue.length; k++) {
    const w = queue[(start + k + queue.length) % queue.length];
    if (w && w.api10 !== current && wellStatus(w) !== "signed") return w.api10;
  }
  return null;
}

/** Neighbour in queue order (step = +1 next, -1 previous), wrapping. */
export function stepQueue(
  queue: QueueWell[],
  current: string | null,
  step: 1 | -1,
): string | null {
  if (queue.length === 0) return null;
  const i = current === null ? -1 : queue.findIndex((w) => w.api10 === current);
  const j =
    i < 0
      ? step === 1
        ? 0
        : queue.length - 1
      : (i + step + queue.length) % queue.length;
  return queue[j]?.api10 ?? null;
}

/** Unsigned wells with no flag beyond at_bound — the bulk-accept set. */
export function cleanUnsigned(queue: QueueWell[]): QueueWell[] {
  return queue.filter((w) => wellRank(w) === 2);
}
