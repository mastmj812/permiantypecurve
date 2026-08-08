// Cohort provenance event construction — pure helpers, split from the
// zustand store so vitest can exercise the AOI-attribution rules
// without touching persist/localStorage.
//
// Lesson baked in from the first real build-up export (bro_time
// 2026-08-06): the AOI only existed when staged wells entered the
// cohort THROUGH the cohort bar right after a lasso. The engineer's
// actual habit — lasso, inspect in the gun-barrel, add from the modal —
// dropped the polygon, and re-lassoing wells already in the cohort
// logged nothing, leaving no retrofit path. Rules now:
//
//   * Any staged well that came from the last draw attributes the
//     draw's polygon — whether or not the well is already a member.
//     Re-lassoing an existing cohort is therefore a sanctioned way to
//     STAMP an AOI onto it (adds nothing, records the polygon).
//   * One event per physical draw: `LastDraw.at` is unique per draw,
//     so clicking "Add staged" twice on the same lasso can't
//     double-log the polygon.
//   * click_add events are logged for FRESH wells only — re-clicking
//     members carries no AOI value and would just spam the narrative.

import type { LastDraw, SelectionEvent } from "../api/types";

export interface CohortProvenance {
  version: 1;
  events: SelectionEvent[];
  // Set when the event cap dropped older entries.
  truncated?: boolean;
}

// localStorage guard: polygons are ~50 vertices, so 100 events is
// comfortably a few hundred KB across all cohorts. Oldest drop first;
// AOI fidelity degrades gracefully (the sheet says "narrative
// truncated" rather than losing the universe, which is server-side).
export const MAX_EVENTS_PER_COHORT = 100;

export function buildStagedEvents(args: {
  // Everything the user just staged (members and non-members alike).
  staged: string[];
  // The subset not already in the cohort.
  fresh: string[];
  lastDraw: LastDraw | null;
  // The cohort's existing narrative — used for the per-draw dedupe.
  priorEvents: SelectionEvent[];
}): SelectionEvent[] {
  const events: SelectionEvent[] = [];
  let clickRest = args.fresh;

  if (args.lastDraw) {
    const d = args.lastDraw;
    const drawSet = new Set(d.api10s);
    const fromDraw = args.staged.filter((a) => drawSet.has(a));
    const alreadyLogged = args.priorEvents.some(
      (e) => (e.kind === "polygon" || e.kind === "bbox") && e.at === d.at,
    );
    if (fromDraw.length > 0 && !alreadyLogged) {
      events.push({
        kind: d.kind,
        at: d.at,
        api10s: fromDraw,
        polygon: d.polygon,
        filters: d.filters,
      });
    }
    clickRest = args.fresh.filter((a) => !drawSet.has(a));
  }

  if (clickRest.length > 0) {
    events.push({
      kind: "click_add",
      at: new Date().toISOString(),
      api10s: clickRest,
      polygon: null,
      filters: args.lastDraw?.filters ?? {},
    });
  }
  return events;
}

// Append events under the per-cohort cap, oldest dropped first.
export function appendEvents(
  prov: CohortProvenance,
  events: SelectionEvent[],
): CohortProvenance {
  if (events.length === 0) return prov;
  let next = [...prov.events, ...events];
  let truncated = prov.truncated;
  if (next.length > MAX_EVENTS_PER_COHORT) {
    next = next.slice(next.length - MAX_EVENTS_PER_COHORT);
    truncated = true;
  }
  return { version: 1, events: next, ...(truncated ? { truncated } : {}) };
}
