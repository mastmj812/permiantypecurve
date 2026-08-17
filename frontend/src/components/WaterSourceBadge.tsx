// Water-stream provenance surfaces. Two components:
//
//   * WaterSourceBadge — per-well pill wherever the water stream shows
//     (Review flags column, forecast detail modal). "calculated" reads
//     as a caution: TX public water is mostly a vendor formula (a
//     static WOR x oil — 83.6% of TX public-water horizontals have a
//     dead-flat WOR), so the water fit inherits a fabricated stream.
//     "measured" reads as trustworthy; indeterminate / insufficient /
//     no-data are neutral muted.
//
//   * WaterSourceCompositionChip — cohort-level "water: N measured /
//     N calculated / N other" line wherever a type curve's water
//     stream is being viewed, so the engineer sees at a glance how
//     much of the water fit rests on calculated data.
//
// CONVENTION OF RECORD (2026-08-17): FLAG ONLY — badge and filter;
// nothing is auto-excluded from any fit or cohort by these fields.

import { useQuery } from "@tanstack/react-query";

import { fetchWaterSourceComposition } from "../api/wells";

const BADGE_TITLES: Record<string, string> = {
  calculated:
    "Vendor-CALCULATED water: the public water series is a static WOR x oil " +
    "formula, not measurement — the water fit inherits a fabricated stream. " +
    "Flag only; the well is not excluded from anything.",
  measured:
    "Measured water: the reported WOR varies like a real produced stream — " +
    "the water fit rests on measurement.",
  indeterminate:
    "Indeterminate water provenance: the WOR pattern doesn't clearly read " +
    "as measured or vendor-calculated.",
  insufficient:
    "Insufficient history to classify the water stream's provenance.",
  no_data: "No water QC data — the well is absent from the warehouse's " +
    "water_data_quality matview (no producing months).",
};

export function WaterSourceBadge({
  source,
  worCv,
  showNoData = false,
}: {
  // wells.water_source — null = no data.
  source: string | null | undefined;
  // wells.wor_cv, appended to the tooltip when present (near-zero =
  // dead-flat WOR, the calculated signature).
  worCv?: number | null;
  // Table cells stay quiet on missing data; the detail modal passes
  // true so "no data" is stated rather than silently blank.
  showNoData?: boolean;
}) {
  const key = source ?? (showNoData ? "no_data" : null);
  if (key == null) return null;
  const cls =
    key === "calculated"
      ? "badge badge-warn"
      : key === "measured"
        ? "badge badge-ok"
        : "badge badge-muted";
  const label = key === "no_data" ? "water: no data" : `water: ${key}`;
  const title =
    (BADGE_TITLES[key] ?? `Water provenance: ${key}`) +
    (worCv != null ? ` (WOR CV ${worCv.toFixed(2)})` : "");
  return (
    <span className={cls} title={title}>
      {label}
    </span>
  );
}

export function WaterSourceCompositionChip({
  api10s,
}: {
  // Cohort membership (the wells backing the displayed curve).
  api10s: string[];
}) {
  // Sorted key so member-order churn doesn't refetch.
  const sortedKey = [...api10s].sort().join(",");
  const q = useQuery({
    queryKey: ["waterSourceComposition", sortedKey],
    queryFn: () => fetchWaterSourceComposition(api10s),
    staleTime: 60_000,
    enabled: api10s.length > 0,
  });
  const c = q.data;
  if (!c || c.total === 0) return null;
  const other = c.indeterminate + c.insufficient + c.no_data;
  const parts = [
    `${c.measured} measured`,
    `${c.calculated} calculated`,
    ...(other > 0 ? [`${other} other`] : []),
  ];
  return (
    <span
      className={`badge ${c.calculated > 0 ? "badge-warn" : "badge-muted"}`}
      title={
        "Water-stream provenance of the cohort — how much of the water fit " +
        "rests on vendor-calculated (static WOR x oil) data. Breakdown: " +
        `${c.measured} measured, ${c.calculated} calculated, ` +
        `${c.indeterminate} indeterminate, ${c.insufficient} insufficient, ` +
        `${c.no_data} no data. Flag only — no well is excluded by this.`
      }
    >
      water: {parts.join(" / ")}
    </span>
  );
}
