// Formation color palette + filter grouping.
//
// The same palette feeds the MapLibre `match` expression for wellstick
// color AND the swatches in the legend / filter panel. One source of
// truth — update this file and both move together.
//
// Names match Novi's ``curated.wells_enriched.formation`` column
// EXACTLY (uppercase, with every observed punctuation / qualifier
// variant from real Loving / Reeves / Ward / Winkler data). MapLibre's
// `match` expression does case-sensitive string equality; any
// formation not listed here falls through to OTHER_COLOR (slate).

export type FormationGroup = "Wolfcamp" | "Bone Spring" | "Spraberry" | "Other";

export interface FormationDef {
  name: string;
  group: FormationGroup;
  color: string;
}

// Delaware + Midland Basin targets, ordered by approximate stratigraphic
// depth (shallowest → deepest) within each group. Warm tones for the
// Wolfcamp family, blues for Bone Spring, indigos for Avalon (which is
// part of the Bone Spring system), greens for Spraberry, purples /
// fuchsias / pinks for "other" (Delaware Sand, Pennsylvanian,
// Woodford, etc).
//
// Source of truth for the strings: ``SELECT DISTINCT formation FROM
// wells`` against the engineering_db-backed wells table. Update this
// list when a new formation string shows up (the map falls back to
// grey for unmatched values, which is the cue).
export const FORMATIONS: FormationDef[] = [
  // ===== Wolfcamp (warm — orange / red / brown family) =====
  // Top-of-section first; "A (XY)" is the same interval as "A" with
  // Enverus' XY qualifier carried over by Novi for some operators.
  { name: "WOLFCAMP A", group: "Wolfcamp", color: "#f97316" },
  { name: "WOLFCAMP A (XY)", group: "Wolfcamp", color: "#fb923c" },
  { name: "WOLFCAMP A (XY) SHELF", group: "Wolfcamp", color: "#fdba74" },
  { name: "WOLFCAMP B", group: "Wolfcamp", color: "#ef4444" },
  { name: "WOLFCAMP C", group: "Wolfcamp", color: "#b91c1c" },
  { name: "WOLFCAMP D", group: "Wolfcamp", color: "#7c2d12" },
  { name: "WOLFCAMP", group: "Wolfcamp", color: "#dc2626" },
  // Cline is colloquially treated as deep Wolfcamp by most Permian
  // engineers — darkest brown.
  { name: "CLINE", group: "Wolfcamp", color: "#5b1b0a" },

  // ===== Bone Spring (cool blue family) =====
  { name: "FIRST BONE SPRING", group: "Bone Spring", color: "#3b82f6" },
  { name: "FIRST BONE SPRING LIME", group: "Bone Spring", color: "#93c5fd" },
  { name: "SECOND BONE SPRING", group: "Bone Spring", color: "#2563eb" },
  { name: "SECOND BONE SPRING LIME", group: "Bone Spring", color: "#60a5fa" },
  { name: "THIRD BONE SPRING", group: "Bone Spring", color: "#0891b2" },
  { name: "THIRD BONE SPRING LIME", group: "Bone Spring", color: "#67e8f9" },
  // Plain "BONE SPRING" — operator didn't specify the bench.
  { name: "BONE SPRING", group: "Bone Spring", color: "#1d4ed8" },
  { name: "BONE SPRING LIME", group: "Bone Spring", color: "#6366f1" },
  { name: "LEONARD", group: "Bone Spring", color: "#4f46e5" },

  // ----- Avalon (Bone Spring system — indigo subfamily) -----
  { name: "UPPER AVALON", group: "Bone Spring", color: "#a5b4fc" },
  { name: "AVALON MIDDLE CARBONATE", group: "Bone Spring", color: "#818cf8" },
  { name: "LOWER AVALON", group: "Bone Spring", color: "#6366f1" },

  // ===== Spraberry (Midland Basin — green / lime family) =====
  { name: "UPPER SPRABERRY", group: "Spraberry", color: "#86efac" },
  { name: "MIDDLE SPRABERRY", group: "Spraberry", color: "#22c55e" },
  { name: "LOWER SPRABERRY SAND", group: "Spraberry", color: "#15803d" },
  { name: "LOWER SPRABERRY SHALE", group: "Spraberry", color: "#16a34a" },
  { name: "SPRABERRY", group: "Spraberry", color: "#65a30d" },
  { name: "JO MILL", group: "Spraberry", color: "#84cc16" },
  { name: "DEAN", group: "Spraberry", color: "#4d7c0f" },

  // ===== Other (vertical / older / non-mainstream — purple-magenta-pink) =====
  { name: "SAN ANDRES", group: "Other", color: "#a855f7" },
  { name: "PADDOCK", group: "Other", color: "#d946ef" },
  { name: "MISSISSIPPIAN", group: "Other", color: "#6b21a8" },
  { name: "DELAWARE", group: "Other", color: "#c026d3" },
  { name: "SUB-WOODFORD", group: "Other", color: "#7e22ce" },
  { name: "WICHITA", group: "Other", color: "#9333ea" },
  { name: "STRAWN", group: "Other", color: "#be185d" },
  { name: "BARNETT", group: "Other", color: "#ec4899" },
  { name: "PENNSYLVANIAN", group: "Other", color: "#7c3aed" },
  { name: "WOODFORD", group: "Other", color: "#701a75" },
  { name: "BLINEBRY", group: "Other", color: "#581c87" },
  { name: "TUBB", group: "Other", color: "#4c1d95" },
  { name: "CLEAR FORK", group: "Other", color: "#c084fc" },
  { name: "GRAYBURG", group: "Other", color: "#e879f9" },
  { name: "ABO", group: "Other", color: "#a21caf" },
  { name: "MCKNIGHT", group: "Other", color: "#86198f" },
  { name: "CANYON", group: "Other", color: "#f0abfc" },
  { name: "JUDKINS", group: "Other", color: "#db2777" },
  { name: "GLORIETA", group: "Other", color: "#be123c" },
  { name: "ELLENBURGER", group: "Other", color: "#1e1b4b" },
  { name: "DELAWARE MOUNTAIN GROUP", group: "Other", color: "#4338ca" },
  { name: "SAN ANGELO", group: "Other", color: "#831843" },
  // UNKNOWN matches the fallback color so the map and the legend
  // agree visually — a well with no formation assigned reads as
  // "other / unknown" in both places.
  { name: "UNKNOWN", group: "Other", color: "#6b7280" },
];

export const OTHER_COLOR = "#6b7280"; // slate-500, fallback for any unmapped formation

// Case-insensitive index so a stray Title-Case or mixed-case
// formation string from a one-off ingest path still resolves to the
// right color. The map's MapLibre `match` expression remains case-
// sensitive (it can't run a function per feature), so the
// formationMatchPairs list still emits the canonical uppercase
// strings — but the legend / filter panel call colorForFormation
// directly and benefit from the looser lookup.
const _COLOR_BY_UPPER: Map<string, string> = new Map(
  FORMATIONS.map((f) => [f.name.toUpperCase(), f.color]),
);

export function colorForFormation(name: string | null | undefined): string {
  if (!name) return OTHER_COLOR;
  return _COLOR_BY_UPPER.get(name.toUpperCase()) ?? OTHER_COLOR;
}

// Flatten [name, color] pairs for the MapLibre `match` expression. MapLibre
// requires the literal sequence value1, output1, value2, output2, ..., fallback.
export function formationMatchPairs(): (string | string[])[] {
  const pairs: (string | string[])[] = [];
  for (const f of FORMATIONS) {
    pairs.push(f.name, f.color);
  }
  return pairs;
}

export function groupedFormations(): Record<FormationGroup, FormationDef[]> {
  const groups: Record<FormationGroup, FormationDef[]> = {
    Wolfcamp: [],
    "Bone Spring": [],
    Spraberry: [],
    Other: [],
  };
  for (const f of FORMATIONS) groups[f.group].push(f);
  return groups;
}
