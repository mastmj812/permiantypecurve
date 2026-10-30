// Text filter for the drop modal's narvi scenario list. That list
// holds every saved scenario across all deals, so a pinned selection
// for this deal can sit far below the fold (RIP Harambe 2026-10-29: a
// superseded dsu_49_50_5 stayed selected and blocked every save).
//
// Matching ignores case and every non-alphanumeric character, so
// "ripharambe", "rip harambe" and "RIP_HARAMBE" all find
// rip_harambe_dsu_15_22. Whitespace splits the query into terms that
// must ALL match ("harambe 49" → the 49_50_5 variants); each term is
// checked against the scenario name, deal_id and scenario_id.

const squash = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "");

export interface SearchableScenario {
  deal_id: string;
  scenario_id: string;
  name?: string | null;
}

/** Normalized query terms; [] = no filter. */
export function searchTerms(query: string): string[] {
  return query.split(/\s+/).map(squash).filter(Boolean);
}

export function scenarioMatches(s: SearchableScenario, terms: readonly string[]): boolean {
  if (terms.length === 0) return true;
  const hay = [s.name ?? "", s.deal_id, s.scenario_id].map(squash);
  return terms.every((t) => hay.some((h) => h.includes(t)));
}
