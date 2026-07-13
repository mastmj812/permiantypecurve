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
  api10s: string[];
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
    initial_api10s?: string[];
  }) => string;
  setActive: (id: string | null) => void;
  rename: (id: string, name: string) => void;
  setDeal: (id: string, deal_id: string | null) => void;
  // Additive — union with existing api10s, preserves insertion order
  // (older wells stay at the front of the list).
  addApi10s: (id: string, api10s: string[]) => void;
  // Subtractive — removes any matching api10s from the cohort.
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

      createCohort: ({ name, deal_id = null, initial_api10s = [] }) => {
        const id = crypto.randomUUID();
        const cohort: Cohort = {
          id,
          name,
          deal_id,
          api10s: Array.from(new Set(initial_api10s)),
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

      addApi10s: (id, api10s) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) =>
            c.id === id
              ? { ...c, api10s: unionPreservingOrder(c.api10s, api10s) }
              : c,
          ),
        })),

      removeApi10s: (id, api10s) => {
        const drop = new Set(api10s);
        set((s) => ({
          cohorts: s.cohorts.map((c) =>
            c.id === id
              ? { ...c, api10s: c.api10s.filter((a) => !drop.has(a)) }
              : c,
          ),
        }));
      },

      replaceApi10s: (id, api10s) =>
        set((s) => ({
          cohorts: s.cohorts.map((c) =>
            c.id === id ? { ...c, api10s: Array.from(new Set(api10s)) } : c,
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
      // Both migrations run when needed; de-dupes after truncation in
      // case a cohort had two completions on the same wellbore (different
      // api14s, same api10).
      version: 3,
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

        if (fromVersion < 2) {
          // v0/v1 → v3: rename api14s → api10s and truncate.
          const s = persistedState as {
            cohorts?: Array<Record<string, unknown> & { api14s?: string[] }>;
            activeCohortId?: string | null;
          };
          const migratedCohorts =
            s.cohorts?.map((c) => {
              const { api14s, ...rest } = c;
              return {
                ...rest,
                api10s: normalize(api14s ?? []),
              };
            }) ?? [];
          return {
            cohorts: migratedCohorts,
            activeCohortId: s.activeCohortId ?? null,
          };
        }
        if (fromVersion < 3) {
          // v2 → v3: truncate any 14-char leftovers in api10s.
          const s = persistedState as {
            cohorts?: Array<Record<string, unknown> & { api10s?: string[] }>;
            activeCohortId?: string | null;
          };
          const migratedCohorts =
            s.cohorts?.map((c) => ({
              ...c,
              api10s: normalize(c.api10s ?? []),
            })) ?? [];
          return {
            cohorts: migratedCohorts,
            activeCohortId: s.activeCohortId ?? null,
          };
        }
        return persistedState as CohortState;
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
