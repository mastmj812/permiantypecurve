// Shared helpers for the slide-export wrappers around TypeCurveChart.
//
// `buildAlignedWellHistories` prepares the gray per-well overlays the
// slide layers behind P50/Mean/TC. Two things happen:
//   1. Peak-align each well by finding argmax(history_rate) on oil and
//      slicing from there forward (the screenshot's x-axis is peak-
//      aligned at 0). We use the oil-rate peak as the alignment anchor
//      for every stream/series so the user sees them register together.
//   2. Normalize per 10,000 ft lateral so per-well lines land in the
//      same band as the P50 (which is already 1k-lateral-ft-normalized
//      on the saved type curve, scaled by 10 here for parity with the
//      screenshot's "Normalized Oil" axis).

import type { Stream, WellCurvesResponse } from "../../api/forecasts";

type CurvesField =
  | "history_rate"
  | "history_cum"
  | "history_rate_filtered"
  | "history_cum_filtered";

// All three stream slides share t=0 = the well's oil-rate peak — that's
// the cohort's convention for first-prod / peak-month alignment. The
// `plotStream` argument selects which stream's series provides the
// PLOTTED values (rate or cum); peak detection stays on oil regardless.
const ANCHOR_STREAM: Stream = "oil";

export function buildAlignedWellHistories(
  curves: WellCurvesResponse[],
  lateralByApi10: Map<string, number | null>,
  field: CurvesField,
  plotStream: Stream = "oil",
  // peak_ramp alignment: keep this many months of REAL pre-peak
  // history in front of the peak, so each well's peak lands at chart
  // month `lookbackMonths` — the same index the curve's bands peak at
  // (the fitted peak_index). Wells with shorter ramps front-pad with
  // nulls. 0 = legacy behavior (peak at month 0).
  lookbackMonths: number = 0,
): Array<{ api10: string; rate: Array<number | null> }> {
  const out: Array<{ api10: string; rate: Array<number | null> }> = [];
  for (const wc of curves) {
    const anchor = wc.streams.find((s) => s.stream === ANCHOR_STREAM);
    const plot = wc.streams.find((s) => s.stream === plotStream);
    if (!anchor || !plot) continue;
    // Argmax over the oil rate, ignoring leading nulls. If a well has
    // no finite peak (all-null history), skip it — it can't anchor.
    const anchorRate = anchor.history_rate;
    let peakIdx = -1;
    let peakVal = -Infinity;
    for (let i = 0; i < anchorRate.length; i++) {
      const v = anchorRate[i];
      if (v != null && Number.isFinite(v) && v > peakVal) {
        peakVal = v;
        peakIdx = i;
      }
    }
    if (peakIdx < 0) continue;

    const source = plot[field];
    // For cum, every entry pre-peak is sunk cum we don't want bleeding
    // into the "month-0 starts at zero" convention of the slide chart.
    // Subtract the cum value at the peak so the well's cum trace starts
    // at 0 at peak month and grows from there.
    // Window start: peak minus the lookback. Negative start months
    // (well has less pre-peak history than the lookback) render as
    // leading nulls so the peak still lands at month `lookbackMonths`.
    const start = peakIdx - lookbackMonths;
    const isCumField =
      field === "history_cum" || field === "history_cum_filtered";
    const baseline = isCumField
      ? (() => {
          // First non-null cum at/after the window start — the
          // filtered cum nulls out downtime months, so the boundary
          // value itself could be null. With a lookback the cum then
          // starts at 0 at the window start (ramp volume included),
          // matching the band cum convention.
          for (let k = Math.max(0, start); k < source.length; k++) {
            const v = source[k];
            if (v != null && Number.isFinite(v)) return v;
          }
          return 0;
        })()
      : 0;

    const lat = lateralByApi10.get(wc.api10) ?? null;
    // 10k-ft normalization. When lateral is missing, don't scale — the
    // raw trace still informs shape and the reader can see it's an
    // un-normalized line; better than dropping the well entirely.
    const scale = lat && lat > 0 ? 10000 / lat : 1;

    const aligned: Array<number | null> = [];
    for (let i = start; i < source.length; i++) {
      const v = i >= 0 ? source[i] : null;
      if (v == null || !Number.isFinite(v)) {
        aligned.push(null);
        continue;
      }
      aligned.push((v - baseline) * scale);
    }
    out.push({ api10: wc.api10, rate: aligned });
  }
  return out;
}
