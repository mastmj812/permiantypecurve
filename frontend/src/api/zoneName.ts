// Blue Ox zone-name grammar — mirror of backend/app/exports/zone_name.py
// (Michael 2026-10-14, after the Snare drop shipped a `W CB_2` tab).
// A zone name IS the workbook tab name and Blue Ox's stable zone key:
// the formation_blueox code of one of the zone's benches, optionally
// + `_SUFFIX` for several zones on one bench (WCB_2_NW). A multi-bench
// zone may carry the bare family tag (WCA for WCA_1 + WCA_2). Letters,
// digits, single underscores; no curve-version tags. Both test suites
// pin the same golden table — change the backend first.

export const GRANDFATHERED_ZONE_NAMES: ReadonlySet<string> = new Set([
  "WCB_2 West",
  "WCB_2 East",
]);

const TOKEN_RE = /^[A-Za-z0-9]+$/;
const VERSION_RE = /^v\d+$/i;

export function zoneNameHeads(benches: readonly string[]): Set<string> {
  const heads = new Set<string>();
  for (const b of benches) {
    const parts = b.split("_");
    for (let k = 1; k <= parts.length; k++) heads.add(parts.slice(0, k).join("_"));
  }
  return heads;
}

export function zoneNameError(name: string, benches: readonly string[]): string | null {
  if (GRANDFATHERED_ZONE_NAMES.has(name)) return null;
  if (!name) return "zone name is empty";
  const tokens = name.split("_");
  if (!tokens.every((t) => TOKEN_RE.test(t))) {
    return `zone name '${name}': letters, digits and single underscores only (no spaces, hyphens, or leading/trailing/double underscores)`;
  }
  if (tokens.some((t) => VERSION_RE.test(t))) {
    return `zone name '${name}': version tags (_v2) belong on the curve name, not the zone — the zone name is the formation tag Blue Ox keys on`;
  }
  if (benches.length === 0) return null;
  if (zoneNameHeads(benches).has(name)) return null;
  if (benches.some((b) => name.startsWith(`${b}_`))) return null;
  const tags = [...benches].sort().join(", ");
  return `zone name '${name}' must be the zone's formation_blueox tag (${tags}) plus an optional _SUFFIX, e.g. WCB_2 or WCB_2_NW`;
}

function family(benches: readonly string[]): string {
  const split = benches.map((b) => b.split("_"));
  const n = Math.min(...split.map((p) => p.length));
  const common: string[] = [];
  for (let k = 0; k < n; k++) {
    const part = split[0]?.[k];
    if (part === undefined || !split.every((p) => p[k] === part)) break;
    common.push(part);
  }
  return common.length > 0 ? common.join("_") : (benches[0] ?? "");
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export function defaultZoneName(
  curveName: string,
  benches: readonly string[],
  shared: boolean,
): string | null {
  const bench = benches[0];
  if (bench === undefined) return null;
  if (benches.length > 1) return family(benches);
  if (!shared) return bench;
  const pat = bench.split("_").map(escapeRe).join("_?");
  const m = new RegExp(`(?:^|_)(${pat})(?:_|$)`, "i").exec(curveName);
  if (!m || m[1] === undefined) return null;
  const end = m.index + m[0].indexOf(m[1]) + m[1].length;
  const rest = curveName.slice(end).split("_").filter(Boolean);
  const suffix = rest.filter((t) => !VERSION_RE.test(t));
  if (suffix.length === 0 || !suffix.every((t) => TOKEN_RE.test(t))) return null;
  return [bench, ...suffix].join("_");
}
