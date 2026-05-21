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

export interface Cohort {
  id: string;
  name: string;
  // Optional target deal — pre-assigned at cohort creation so the
  // eventual saved type curve auto-fills its deal dropdown. The cohort
  // doesn't show up under the deal in the Deals sidebar until a type
  // curve has actually been saved against it (per the design contract).
  deal_id: string | null;
  api14s: string[];
  created_at: string; // ISO timestamp
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
    initial_api14s?: string[];
  }) => string;
  setActive: (id: string | null) => void;
  rename: (id: string, name: string) => void;
  setDeal: (id: string, deal_id: string | null) => void;
  // Additive — union with existing api14s, preserves insertion order
  // (older wells stay at the front of the list).
  addApi14s: (id: string, api14s: string[]) => void;
  // Subtractive — removes any matching api14s from the cohort.
  removeApi14s: (id: string, api14s: string[]) => void;
  // Destructive — replaces the cohort's api14s wholesale. Currently
  // unused by the cohort bar but available for future "replace cohort
  // with current staged" UX if we want it.
  replaceApi14s: (id: string, api14s: string[]) => void;
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

      createCohort: ({ name, deal_id = null, initial_api14s = [] }) => {
        const id = crypto.randomUUID();
        const cohort: Cohort = {
          id,
          name,
          deal_id,
          api14s: Array.from(new Set(initial_api14s)),
          created_at: new Date().toISOString(),
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

      addApi14s: (id, api14s) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) =>
            c.id === id
              ? { ...c, api14s: unionPreservingOrder(c.api14s, api14s) }
              : c,
          ),
        })),

      removeApi14s: (id, api14s) => {
        const drop = new Set(api14s);
        set((s) => ({
          cohorts: s.cohorts.map((c) =>
            c.id === id
              ? { ...c, api14s: c.api14s.filter((a) => !drop.has(a)) }
              : c,
          ),
        }));
      },

      replaceApi14s: (id, api14s) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) =>
            c.id === id ? { ...c, api14s: Array.from(new Set(api14s)) } : c,
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
      version: 1,
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
