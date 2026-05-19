// Formation color palette + filter grouping.
//
// The same palette feeds the MapLibre `match` expression for wellstick
// color AND the swatches in the legend / filter panel. One source of
// truth — update this file and both move together.
//
// Names match Enverus' `ENVInterval` column EXACTLY (uppercase, with
// explicit LOWER/UPPER subdivisions where Enverus splits them).
// MapLibre's `match` expression does case-sensitive string equality;
// any formation not listed here falls through to OTHER_COLOR (grey).

export type FormationGroup = "Wolfcamp" | "Bone Spring" | "Spraberry" | "Other";

export interface FormationDef {
  name: string;
  group: FormationGroup;
  color: string;
}

// Delaware + Midland Basin targets, ordered by approximate stratigraphic
// depth (shallowest → deepest) within each group. Warm tones for the
// Wolfcamp family, blues for Bone Spring, indigos for Avalon (which is
// part of the Bone Spring system), greens for Spraberry, purples for
// "other" (Delaware Sand, Pennsylvanian, Woodford, etc).
// Names match Enverus' `ENVInterval` column EXACTLY (uppercase, with
// every observed punctuation / abbreviation variant from real Loving
// County data — Enverus is inconsistent so we list each variant
// explicitly). New formations should be added here as they're observed.
export const FORMATIONS: FormationDef[] = [
  // ----- Avalon (Delaware Basin, Bone Spring system — indigo family) -----
  { name: "ABOVE UPPER AVALON", group: "Bone Spring", color: "#c7d2fe" },
  { name: "UPPER AVALON", group: "Bone Spring", color: "#a5b4fc" },
  { name: "MIDDLE AVALON", group: "Bone Spring", color: "#818cf8" },
  { name: "LOWER AVALON", group: "Bone Spring", color: "#6366f1" },
  { name: "AVALON", group: "Bone Spring", color: "#4f46e5" },

  // ----- Bone Spring (Delaware Basin, blue family) -----
  // Plain (no sand/carbonate suffix) uses the same hue as SAND since SAND
  // is the more common interval — swatches will look the same as the
  // SAND variant, which is fine.
  { name: "1ST BONE SPRING", group: "Bone Spring", color: "#3b82f6" },
  { name: "1ST BONE SPRING SAND", group: "Bone Spring", color: "#3b82f6" },
  { name: "1ST BONE SPRING CARBONATE", group: "Bone Spring", color: "#60a5fa" },
  { name: "2ND BONE SPRING", group: "Bone Spring", color: "#2563eb" },
  { name: "2ND BONE SPRING SAND", group: "Bone Spring", color: "#2563eb" },
  { name: "2ND BONE SPRING CARBONATE", group: "Bone Spring", color: "#0ea5e9" },
  { name: "3RD BONE SPRING", group: "Bone Spring", color: "#0891b2" },
  { name: "3RD BONE SPRING SAND", group: "Bone Spring", color: "#0891b2" },
  { name: "3RD BONE SPRING CARBONATE", group: "Bone Spring", color: "#0d9488" },
  { name: "BONE SPRING", group: "Bone Spring", color: "#1d4ed8" },
  // Dual-interval wells — color as Bone Spring since it's the shallower
  // of the two and likely the primary producer.
  { name: "BONE SPRING,WOLFCAMP", group: "Bone Spring", color: "#1e40af" },

  // ----- Spraberry (Midland Basin — green family, not in Loving data
  // but kept for future Midland-county work) -----
  { name: "UPPER SPRABERRY", group: "Spraberry", color: "#86efac" },
  { name: "MIDDLE SPRABERRY", group: "Spraberry", color: "#22c55e" },
  { name: "LOWER SPRABERRY", group: "Spraberry", color: "#15803d" },
  { name: "JO MILL", group: "Spraberry", color: "#65a30d" },
  { name: "DEAN", group: "Spraberry", color: "#84cc16" },
  { name: "SPRABERRY", group: "Spraberry", color: "#16a34a" },

  // ----- Wolfcamp (both basins — warm family) -----
  // "WOLFCAMP XY" and "WOLFCAMP X+Y" are the same interval, just
  // different Enverus spellings. Same color so the map reads consistently.
  { name: "WOLFCAMP XY", group: "Wolfcamp", color: "#fb923c" },
  { name: "WOLFCAMP X+Y", group: "Wolfcamp", color: "#fb923c" },
  { name: "WOLFCAMP A UPPER", group: "Wolfcamp", color: "#f97316" },
  { name: "WOLFCAMP A LOWER", group: "Wolfcamp", color: "#d97706" },
  { name: "WOLFCAMP B UPPER", group: "Wolfcamp", color: "#ef4444" },
  { name: "WOLFCAMP B LOWER", group: "Wolfcamp", color: "#b91c1c" },
  { name: "WOLFCAMP C", group: "Wolfcamp", color: "#9a3412" },
  { name: "WOLFCAMP D", group: "Wolfcamp", color: "#7c2d12" },
  { name: "WOLFCAMP", group: "Wolfcamp", color: "#dc2626" },
  // Deep Wolfcamp / Pennsylvanian-consolidated intervals — use the
  // darkest Wolfcamp tones so they read as the deepest in the family.
  { name: "LWR. WOLFCAMP-PENN CONS.", group: "Wolfcamp", color: "#5b1b0a" },
  { name: "WLFCMP\\PENN CONS", group: "Wolfcamp", color: "#5b1b0a" },

  // ----- Other (non-mainstream / older / vertical Delaware) -----
  // Purples and magentas to stay distinct from the W/BS/Spr palettes.
  // DELAWARE VERTICAL is the dominant non-unconventional target in
  // Loving — vertical wells in the Delaware sand. Engineers typically
  // filter these OUT for unconventional type-curve work.
  { name: "DELAWARE VERTICAL", group: "Other", color: "#a855f7" },
  { name: "DELAWARE,UPPER BELL CAN", group: "Other", color: "#c026d3" },
  { name: "LOWER PENNSYLVANIAN", group: "Other", color: "#7c3aed" },
  { name: "BARNETT", group: "Other", color: "#be185d" },
  { name: "LOWER MISSISSIPPIAN", group: "Other", color: "#581c87" },
  { name: "WOODFORD", group: "Other", color: "#701a75" },
  { name: "LOWER DEVONIAN AND BELOW", group: "Other", color: "#1e1b4b" },
  { name: "TEXAS SHELF ALL", group: "Other", color: "#52525b" },
];

export const OTHER_COLOR = "#6b7280"; // slate-500, fallback for any unmapped formation

export function colorForFormation(name: string | null | undefined): string {
  if (!name) return OTHER_COLOR;
  return FORMATIONS.find((f) => f.name === name)?.color ?? OTHER_COLOR;
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
