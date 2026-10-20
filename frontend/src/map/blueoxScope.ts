// Blue Ox zone scenario-scope upkeep for the drop modal. A zone's
// scenario_scope lists the (deal_id, scenario_id) pairs it captures;
// null/[] = every selected scenario (backend scope_pairs() → None).
// The backend 422s a save whose scope names a pair that is not in
// narvi_selections, so deselecting a scenario must also prune it from
// the scopes — but a scope must never prune down to [] on its own,
// because [] reads as "all" and would silently WIDEN the zone onto
// every other selected scenario (wrong curve on those wells, in a
// finance deliverable).
//
// Explicit behavior: a zone whose every scope ref was deselected keeps
// those refs (dangling). Dangling-only scope covers no selected
// scenario (0 wells, no category signal), the row warns, and save is
// blocked until the engineer re-scopes it — check the scenarios it
// should capture, or "all scenarios". Re-selecting the scenario heals
// it. Zones that still have a live ref just drop the dead ones.

import type { BlueOxScenarioRef, BlueOxZoneSpec } from "../api/deals";

const sameRef = (a: BlueOxScenarioRef, b: BlueOxScenarioRef) =>
  a.deal_id === b.deal_id && a.scenario_id === b.scenario_id;

/** Scope refs that name no current selection. Unscoped zones have none. */
export function danglingScopeRefs(
  zone: BlueOxZoneSpec,
  selections: readonly BlueOxScenarioRef[],
): BlueOxScenarioRef[] {
  return (zone.scenario_scope ?? []).filter(
    (r) => !selections.some((s) => sameRef(s, r)),
  );
}

/** Scope refs that DO name a current selection. */
export function liveScopeRefs(
  zone: BlueOxZoneSpec,
  selections: readonly BlueOxScenarioRef[],
): BlueOxScenarioRef[] {
  return (zone.scenario_scope ?? []).filter((r) =>
    selections.some((s) => sameRef(s, r)),
  );
}

/** Drop scope refs that are no longer selected. A scope that would
 * prune to empty is left as-is (dangling — see header), never nulled. */
export function pruneZoneScopes(
  zones: readonly BlueOxZoneSpec[],
  selections: readonly BlueOxScenarioRef[],
): BlueOxZoneSpec[] {
  return zones.map((z) => {
    if (!z.scenario_scope?.length) return z;
    const live = liveScopeRefs(z, selections);
    if (live.length === z.scenario_scope.length || live.length === 0) return z;
    return { ...z, scenario_scope: live };
  });
}

/** Remove one dangling ref from a zone's scope (picker warning row).
 * Refuses (returns null) when it is the zone's last ref — emptying
 * the scope would widen the zone to all scenarios. */
export function dropScopeRef(
  zone: BlueOxZoneSpec,
  ref: BlueOxScenarioRef,
): BlueOxZoneSpec | null {
  const rest = (zone.scenario_scope ?? []).filter((r) => !sameRef(r, ref));
  if (rest.length === 0) return null;
  return { ...zone, scenario_scope: rest };
}
