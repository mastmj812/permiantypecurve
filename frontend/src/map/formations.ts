// formation_blueox color palette + filter grouping (basin-aware).
//
// Coloring keys on (basin_blueox, formation_blueox code): Delaware and Midland
// have INDEPENDENT color scales, but codes that exist in BOTH basins (WCA_1,
// WCB_1, WCB_2, WCC, WCD, STRN, BRNT, MISS, WDFD, OTHER) get the SAME color in
// both. Adjacent benches use CONTRASTING hues (not shades of one color) so they
// read apart on the map. NULL formation_blueox / basin falls back to OTHER_COLOR.
//
// This is a FIRST-PASS palette meant to be refined visually on the live map.
//
// One source of truth: the MapLibre nested `match` (blueoxColorExpression) and
// the legend / filter swatches all derive from BLUEOX_COLORS below.

export type FormationGroup =
  | "Wolfcamp"
  | "Bone Spring"
  | "Avalon"
  | "Spraberry"
  | "Other";

export type BasinBlueox = "delaware" | "midland" | "cbp";

export const OTHER_COLOR = "#9ca3af"; // gray-400, fallback for unmapped / null

// code → group, for legend + filter organization (NOT color family — colors
// contrast within a group on purpose).
export const BLUEOX_GROUP: Record<string, FormationGroup> = {
  WCA_1: "Wolfcamp", WCA_2: "Wolfcamp", WCB_1: "Wolfcamp", WCB_2: "Wolfcamp",
  WCC: "Wolfcamp", WCD: "Wolfcamp", WCXY: "Wolfcamp",
  BS1_S: "Bone Spring", BS2_C: "Bone Spring", BS2_S: "Bone Spring",
  BS3_C: "Bone Spring", BS3_S: "Bone Spring",
  AVA_0: "Avalon", AVA_1: "Avalon", AVA_2: "Avalon",
  US: "Spraberry", MS: "Spraberry", JM: "Spraberry", LSSH: "Spraberry", DEAN: "Spraberry",
  STRN: "Other", BRNT: "Other", MISS: "Other", WDFD: "Other", MRMC: "Other", OTHER: "Other",
};

// Stratigraphic order within each basin (shallow → deep), for legend display.
export const BASIN_CODES: Record<BasinBlueox, string[]> = {
  delaware: [
    "AVA_0", "AVA_1", "AVA_2",
    "BS1_S", "BS2_C", "BS2_S", "BS3_C", "BS3_S",
    "WCXY", "WCA_1", "WCA_2", "WCB_1", "WCB_2", "WCC", "WCD",
    "STRN", "BRNT", "MISS", "WDFD", "OTHER",
  ],
  midland: [
    "US", "MS", "JM", "LSSH", "DEAN",
    "WCA_1", "WCB_1", "WCB_2", "WCC", "WCD",
    "STRN", "BRNT", "MRMC", "MISS", "WDFD", "OTHER",
  ],
  // Central Basin Platform — DEEP UNCONVENTIONAL targets only. The conventional
  // shelf section (San Andres, Grayburg, Clear Fork ...) is bucketed to OTHER
  // upstream (basin defaults unmapped -> OTHER in the warehouse), so it never
  // appears here as its own code.
  cbp: [
    "MS", "LSSH", "DEAN",
    "BS1_S", "BS2_C", "BS2_S",
    "WCB_1", "WCB_2",
    "STRN", "MISS", "BRNT", "WDFD", "OTHER",
  ],
};

// Codes present in both basins — pinned to a single shared color.
const SHARED: Record<string, string> = {
  WCA_1: "#f97316", // orange
  WCB_1: "#22c55e", // green
  WCB_2: "#ec4899", // pink
  WCC: "#8b5cf6",   // violet
  WCD: "#0ea5e9",   // sky
  STRN: "#78716c",  // stone
  BRNT: "#2563eb",  // blue
  MISS: "#dc2626",  // red
  WDFD: "#4d7c0f",  // olive
  OTHER: OTHER_COLOR,
};

// Per-basin code → color. Independent scales: Delaware and Midland may reuse a
// hue for DIFFERENT codes (a well is only in one basin); shared codes match.
export const BLUEOX_COLORS: Record<BasinBlueox, Record<string, string>> = {
  delaware: {
    ...SHARED,
    AVA_0: "#06b6d4", // cyan
    AVA_1: "#f43f5e", // rose
    AVA_2: "#84cc16", // lime
    BS1_S: "#eab308", // yellow
    BS2_C: "#a855f7", // purple
    BS2_S: "#14b8a6", // teal
    BS3_C: "#fb923c", // light orange
    BS3_S: "#d946ef", // fuchsia
    WCXY: "#65a30d",  // dark lime
    WCA_2: "#e11d48", // dark rose
  },
  midland: {
    ...SHARED,
    US: "#06b6d4",   // cyan
    MS: "#f43f5e",   // rose
    JM: "#a855f7",   // purple
    LSSH: "#eab308", // yellow
    DEAN: "#14b8a6", // teal
    MRMC: "#d946ef", // fuchsia
  },
  // CBP deep unconventional only. Shared codes (WCB_*, MISS, BRNT, WDFD, STRN)
  // keep their shared colors; its Bone Spring / Spraberry codes get distinct
  // hues that don't collide with the shared set within this scale.
  cbp: {
    ...SHARED,
    BS1_S: "#eab308", // yellow
    BS2_C: "#a855f7", // purple
    BS2_S: "#14b8a6", // teal
    LSSH: "#06b6d4",  // cyan
    MS: "#f43f5e",    // rose
    DEAN: "#84cc16",  // lime
  },
};

// Flat code → color (basin-agnostic): shared codes resolve to their shared
// color; basin-specific codes to their (unique-within-basin) color. Used for
// the filter swatches / legend chips where there's no basin context. Delaware
// listed last so a Delaware-specific code wins if a code somehow exists in both
// extra maps (none do today).
export const CODE_COLOR: Record<string, string> = {
  ...BLUEOX_COLORS.cbp,
  ...BLUEOX_COLORS.midland,
  ...BLUEOX_COLORS.delaware,
};

// All basins with a nomenclature, in legend display order.
export const BASINS: BasinBlueox[] = ["delaware", "midland", "cbp"];

export function colorForFormation(code: string | null | undefined): string {
  if (!code) return OTHER_COLOR;
  return CODE_COLOR[code] ?? OTHER_COLOR;
}

// Basin-aware color for a single well (map/gun-barrel where basin is known).
export function colorForBlueox(
  basin: string | null | undefined,
  code: string | null | undefined,
): string {
  if (!basin || !code) return OTHER_COLOR;
  return BLUEOX_COLORS[basin as BasinBlueox]?.[code] ?? OTHER_COLOR;
}

export function groupForBlueox(code: string): FormationGroup {
  return BLUEOX_GROUP[code] ?? "Other";
}

// Nested MapLibre `match`: outer on basin_blueox, inner on formation_blueox.
// Returns a JSON expression; the caller casts to ExpressionSpecification.
export function blueoxColorExpression(): unknown {
  const inner = (basin: BasinBlueox): unknown[] => {
    const pairs: unknown[] = [];
    for (const code of BASIN_CODES[basin]) {
      pairs.push(code, BLUEOX_COLORS[basin][code]);
    }
    return ["match", ["get", "formation_blueox"], ...pairs, OTHER_COLOR];
  };
  const branches: unknown[] = [];
  for (const basin of BASINS) {
    branches.push(basin, inner(basin));
  }
  return ["match", ["get", "basin_blueox"], ...branches, OTHER_COLOR];
}

// Legend data: each basin's codes in stratigraphic order, with color + group.
export interface LegendCode {
  code: string;
  color: string;
  group: FormationGroup;
}

// Group display order (shallow → deep by play), shared by the legend and the
// filter panel. Within a group, codes follow CODE_DISPLAY_ORDER (nomenclature).
export const GROUP_DISPLAY_ORDER: FormationGroup[] = [
  "Avalon",
  "Bone Spring",
  "Spraberry",
  "Wolfcamp",
  "Other",
];

// Canonical within-group code order (stratigraphic, shallow → deep). Drives the
// filter panel's per-group ordering; the legend derives within-group order from
// the stratigraphic BASIN_CODES lists directly. Codes not listed sort last.
export const CODE_DISPLAY_ORDER: string[] = [
  "AVA_0", "AVA_1", "AVA_2",                                  // Avalon
  "BS1_S", "BS2_C", "BS2_S", "BS3_C", "BS3_S",                // Bone Spring
  "US", "MS", "JM", "LSSH", "DEAN",                           // Spraberry
  "WCXY", "WCA_1", "WCA_2", "WCB_1", "WCB_2", "WCC", "WCD",   // Wolfcamp
  "STRN", "BRNT", "MRMC", "MISS", "WDFD", "OTHER",            // Other (order n/a)
];

const _CODE_RANK = new Map(CODE_DISPLAY_ORDER.map((c, i) => [c, i]));

export function codeRank(code: string): number {
  return _CODE_RANK.get(code) ?? Number.MAX_SAFE_INTEGER;
}

export function legendByBasin(): Record<BasinBlueox, LegendCode[]> {
  const build = (basin: BasinBlueox): LegendCode[] => {
    const codes = BASIN_CODES[basin].map((code) => ({
      code,
      color: BLUEOX_COLORS[basin][code] ?? OTHER_COLOR,
      group: groupForBlueox(code),
    }));
    // Array.prototype.sort is stable, so equal-group codes keep BASIN_CODES order.
    return codes.sort(
      (a, b) =>
        GROUP_DISPLAY_ORDER.indexOf(a.group) - GROUP_DISPLAY_ORDER.indexOf(b.group),
    );
  };
  return {
    delaware: build("delaware"),
    midland: build("midland"),
    cbp: build("cbp"),
  };
}
