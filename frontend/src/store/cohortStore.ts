// Cohort store — the workflow primitive that makes curve-building
// iterative instead of single-shot. A cohort is a named, accumulating
// well list separate from the transient lasso/box "staging" selection
// in mapStore. Multiple cohorts can be in flight at once; the user
// switches between them from the cohort bar above the map.
//
// Persistence: localStorage only (single-user single-machine tool).
// Cohorts survive browser reloads but don't move between machines.
// Backend persistence is intentionally out of scope for the MVP.

import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { LastDraw } from "../api/types";
// Selection-event narrative for the type-well build-up. Supporting
// narrative only: the build-up waterfall is computed server-side from
// the universe snapshot + filter snapshot + final membership, so an
// incomplete event log degrades the story, never the numbers. The
// polygon-bearing events additionally define the AOI (their union) —
// attribution rules live in cohortProvenance.ts.
import {
  type CohortProvenance,
  appendEvents,
  buildStagedEvents,
} from "./cohortProvenance";

export type { CohortProvenance } from "./cohortProvenance";

export interface Cohort {
  id: string;
  name: string;
  // Optional target deal — pre-assigned at cohort creation so the
  // eventual saved type curve auto-fills its deal dropdown. The cohort
  // doesn't show up under the deal in the Deals sidebar until a type
  // curve has actually been saved against it (per the design contract).
  deal_id: string | null;
  api10s: string[];
  created_at: string; // ISO timestamp
  provenance: CohortProvenance;
}

export interface CohortState {
  cohorts: Cohort[];
  activeCohortId: string | null;

  // ---- mutations ----
  // All mutations return the (possibly new) active cohort id so callers
  // can chain UI flow without re-reading state.
  createCohort: (args: {
    name: string;
    deal_id?: string | null;
    initial_api10s?: string[];
    // When seeding from the staged selection, the draw that produced
    // it — so the seed lands in the event narrative like an addStaged.
    lastDraw?: LastDraw | null;
  }) => string;
  setActive: (id: string | null) => void;
  rename: (id: string, name: string) => void;
  setDeal: (id: string, deal_id: string | null) => void;
  // Additive — union with existing api10s, preserves insertion order
  // (older wells stay at the front of the list). Logs provenance
  // events: staged wells that came from `lastDraw` get a polygon/bbox
  // event carrying the AOI; the remainder (click-adds, gun-barrel
  // adds) get a click_add event. Pass null when there was no draw.
  addStaged: (id: string, api10s: string[], lastDraw: LastDraw | null) => void;
  // Subtractive — removes any matching api10s from the cohort and logs
  // a manual_remove event (build-up narrative for the culled wells).
  removeApi10s: (id: string, api10s: string[]) => void;
  // Destructive — replaces the cohort's api10s wholesale. Currently
  // unused by the cohort bar but available for future "replace cohort
  // with current staged" UX if we want it.
  replaceApi10s: (id: string, api10s: string[]) => void;
  deleteCohort: (id: string) => void;
}

// Helper — preserve order, drop duplicates, returns a new array.
function unionPreservingOrder(existing: string[], incoming: string[]): string[] {
  const seen = new Set(existing);
  const out = [...existing];
  for (const v of incoming) {
    if (!seen.has(v)) {
      seen.add(v);
      out.push(v);
    }
  }
  return out;
}

export const useCohortStore = create<CohortState>()(
  persist(
    (set, get) => ({
      cohorts: [],
      activeCohortId: null,

      createCohort: ({
        name,
        deal_id = null,
        initial_api10s = [],
        lastDraw = null,
      }) => {
        const id = crypto.randomUUID();
        const seeded = Array.from(new Set(initial_api10s));
        const cohort: Cohort = {
          id,
          name,
          deal_id,
          api10s: seeded,
          created_at: new Date().toISOString(),
          provenance: appendEvents(
            { version: 1, events: [] },
            buildStagedEvents({
              staged: seeded,
              fresh: seeded,
              lastDraw,
              priorEvents: [],
            }),
          ),
        };
        set((s) => ({
          cohorts: [...s.cohorts, cohort],
          activeCohortId: id,
        }));
        return id;
      },

      setActive: (id) => set({ activeCohortId: id }),

      rename: (id, name) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) => (c.id === id ? { ...c, name } : c)),
        })),

      setDeal: (id, deal_id) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) => (c.id === id ? { ...c, deal_id } : c)),
        })),

      addStaged: (id, api10s, lastDraw) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) => {
            if (c.id !== id) return c;
            const existing = new Set(c.api10s);
            const fresh = api10s.filter((a) => !existing.has(a));
            // Draw-sourced wells attribute the AOI even when already
            // members (re-lassoing an existing cohort STAMPS its AOI);
            // click_adds log fresh wells only. See cohortProvenance.ts.
            return {
              ...c,
              api10s: unionPreservingOrder(c.api10s, api10s),
              provenance: appendEvents(
                c.provenance,
                buildStagedEvents({
                  staged: api10s,
                  fresh,
                  lastDraw,
                  priorEvents: c.provenance.events,
                }),
              ),
            };
          }),
        })),

      removeApi10s: (id, api10s) => {
        const drop = new Set(api10s);
        const now = new Date().toISOString();
        set((s) => ({
          cohorts: s.cohorts.map((c) => {
            if (c.id !== id) return c;
            const removed = c.api10s.filter((a) => drop.has(a));
            return {
              ...c,
              api10s: c.api10s.filter((a) => !drop.has(a)),
              provenance:
                removed.length > 0
                  ? appendEvents(c.provenance, [
                      {
                        kind: "manual_remove",
                        at: now,
                        api10s: removed,
                        polygon: null,
                        filters: {},
                      },
                    ])
                  : c.provenance,
            };
          }),
        }));
      },

      replaceApi10s: (id, api10s) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) =>
            c.id === id
              ? {
                  ...c,
                  api10s: Array.from(new Set(api10s)),
                  // Wholesale replace restarts the narrative — the old
                  // events describe a list that no longer exists.
                  provenance: { version: 1, events: [] },
                }
              : c,
          ),
        })),

      deleteCohort: (id) => {
        const wasActive = get().activeCohortId === id;
        set((s) => ({
          cohorts: s.cohorts.filter((c) => c.id !== id),
          activeCohortId: wasActive ? null : s.activeCohortId,
        }));
      },
    }),
    {
      // localStorage key. Anything sitting under this key from a prior
      // session loads automatically on first render. Changing the key
      // string is a destructive migration — bump only with intent.
      name: "permian-cohorts",
      // v2: api14 → api10 rename (cutover).
      // v3: truncate any 14-char api14 strings to their leading 10 chars
      //     (the api10 of the same wellbore). The v2 migrate copied the
      //     old api14s array into the new api10s field without
      //     truncating, leaving early adopters with 14-char strings in
      //     a field that should be 10. v3 normalizes that.
      // v4: add empty build-up provenance ({version: 1, events: []}) to
      //     every cohort. Pre-v4 cohorts have no recorded narrative —
      //     honest empty, not fabricated.
      // All migrations run when needed; de-dupes after truncation in
      // case a cohort had two completions on the same wellbore (different
      // api14s, same api10).
      version: 4,
      migrate: (persistedState: unknown, fromVersion: number) => {
        // Helper: truncate + de-dupe a list of api strings.
        const normalize = (raw: string[]): string[] => {
          const seen = new Set<string>();
          const out: string[] = [];
          for (const v of raw) {
            const a10 = v.slice(0, 10);
            if (a10.length === 10 && !seen.has(a10)) {
              seen.add(a10);
              out.push(a10);
            }
          }
          return out;
        };

        let state: {
          cohorts?: Array<Record<string, unknown>>;
          activeCohortId?: string | null;
        };

        if (fromVersion < 2) {
          // v0/v1: rename api14s → api10s and truncate.
          const s = persistedState as {
            cohorts?: Array<Record<string, unknown> & { api14s?: string[] }>;
            activeCohortId?: string | null;
          };
          state = {
            cohorts:
              s.cohorts?.map((c) => {
                const { api14s, ...rest } = c;
                return {
                  ...rest,
                  api10s: normalize(api14s ?? []),
                };
              }) ?? [],
            activeCohortId: s.activeCohortId ?? null,
          };
        } else if (fromVersion < 3) {
          // v2: truncate any 14-char leftovers in api10s.
          const s = persistedState as {
            cohorts?: Array<Record<string, unknown> & { api10s?: string[] }>;
            activeCohortId?: string | null;
          };
          state = {
            cohorts:
              s.cohorts?.map((c) => ({
                ...c,
                api10s: normalize(c.api10s ?? []),
              })) ?? [],
            activeCohortId: s.activeCohortId ?? null,
          };
        } else {
          state = persistedState as typeof state;
        }

        // v4 (applies to every earlier version): ensure provenance.
        return {
          cohorts:
            state.cohorts?.map((c) => ({
              ...c,
              provenance: c.provenance ?? { version: 1, events: [] },
            })) ?? [],
          activeCohortId: state.activeCohortId ?? null,
        };
      },
    },
  ),
);

// Convenience selector — returns the active cohort object or null.
// Components subscribe via `useCohortStore(activeCohort)` for ergonomic
// access without re-implementing the lookup at every call site.
export function activeCohort(s: CohortState): Cohort | null {
  if (!s.activeCohortId) return null;
  return s.cohorts.find((c) => c.id === s.activeCohortId) ?? null;
}
