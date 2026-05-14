// Formation color palette + filter grouping.
//
// The same palette feeds the MapLibre `match` expression for wellstick
// color AND the swatches in the legend / filter panel. One source of truth.

export type FormationGroup = "Wolfcamp" | "Bone Spring" | "Spraberry" | "Other";

export interface FormationDef {
  name: string;
  group: FormationGroup;
  color: string;
}

// Permian-typical targets. Color choices: warm tones for Wolfcamp, cools for
// Bone Spring, greens for Spraberry — readable on the Protomaps "light" base.
export const FORMATIONS: FormationDef[] = [
  { name: "Wolfcamp A", group: "Wolfcamp", color: "#d97706" },
  { name: "Wolfcamp B", group: "Wolfcamp", color: "#b91c1c" },
  { name: "Wolfcamp C", group: "Wolfcamp", color: "#9a3412" },
  { name: "Wolfcamp D", group: "Wolfcamp", color: "#7c2d12" },
  { name: "Wolfcamp X+Y", group: "Wolfcamp", color: "#ef4444" },
  { name: "Bone Spring 1st", group: "Bone Spring", color: "#2563eb" },
  { name: "Bone Spring 2nd", group: "Bone Spring", color: "#0ea5e9" },
  { name: "Bone Spring 3rd", group: "Bone Spring", color: "#0d9488" },
  { name: "Avalon", group: "Bone Spring", color: "#1e40af" },
  { name: "Lower Spraberry", group: "Spraberry", color: "#15803d" },
  { name: "Middle Spraberry", group: "Spraberry", color: "#22c55e" },
];

export const OTHER_COLOR = "#6b7280"; // slate-500, used for unknown formations

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
