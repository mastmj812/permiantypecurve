// EUR-per-lateral-foot outlier detection.
//
// Rule from the brief: flag wells where |EUR/ft - median| > 2σ.
// σ is the sample standard deviation of EUR/ft across the included
// selection. Pure function — no React, no DOM — so it's trivial to test.

export interface OutlierInput {
  api14: string;
  eur: number | null;
  lateral_ft: number | null;
}

export interface OutlierStats {
  values: number[]; // EUR/ft per well (only wells with both EUR and lateral_ft)
  median: number;
  mean: number;
  stddev: number;
  lowThreshold: number;
  highThreshold: number;
}

export interface OutlierResult {
  outlierApi14s: Set<string>;
  perWell: Record<string, { eurPerFt: number | null; isOutlier: boolean }>;
  stats: OutlierStats | null; // null when there's no usable data
}

const OUTLIER_SIGMA: number = 2;

export function computeOutliers(rows: OutlierInput[]): OutlierResult {
  const perWell: Record<string, { eurPerFt: number | null; isOutlier: boolean }> = {};
  const usable: { api14: string; eurPerFt: number }[] = [];

  for (const r of rows) {
    if (r.eur == null || r.lateral_ft == null || r.lateral_ft <= 0) {
      perWell[r.api14] = { eurPerFt: null, isOutlier: false };
      continue;
    }
    const v = r.eur / r.lateral_ft;
    perWell[r.api14] = { eurPerFt: v, isOutlier: false };
    usable.push({ api14: r.api14, eurPerFt: v });
  }

  if (usable.length < 3) {
    // 2σ on a 2-sample population is meaningless; bail rather than flag noise.
    return { outlierApi14s: new Set(), perWell, stats: null };
  }

  const values = usable.map((u) => u.eurPerFt);
  const sorted = [...values].sort((a, b) => a - b);
  const median = sorted.length % 2 === 0
    ? (sorted[sorted.length / 2 - 1]! + sorted[sorted.length / 2]!) / 2
    : sorted[(sorted.length - 1) / 2]!;
  const mean = values.reduce((s, v) => s + v, 0) / values.length;
  // Population stddev (N), not sample (N-1). For outlier detection in a
  // typed-curve selection of 50–500 wells the difference is negligible.
  const variance = values.reduce((s, v) => s + (v - mean) ** 2, 0) / values.length;
  const stddev = Math.sqrt(variance);
  const lowThreshold = median - OUTLIER_SIGMA * stddev;
  const highThreshold = median + OUTLIER_SIGMA * stddev;

  const outlierApi14s = new Set<string>();
  for (const u of usable) {
    if (u.eurPerFt < lowThreshold || u.eurPerFt > highThreshold) {
      outlierApi14s.add(u.api14);
      perWell[u.api14]!.isOutlier = true;
    }
  }

  return {
    outlierApi14s,
    perWell,
    stats: { values, median, mean, stddev, lowThreshold, highThreshold },
  };
}
