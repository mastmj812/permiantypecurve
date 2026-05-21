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

import type { WellCurvesResponse } from "../../api/forecasts";

type CurvesField = "history_rate" | "history_cum";

const STREAM = "oil";

export function buildAlignedWellHistories(
  curves: WellCurvesResponse[],
  lateralByApi14: Map<string, number | null>,
  field: CurvesField,
): Array<{ api14: string; rate: Array<number | null> }> {
  const out: Array<{ api14: string; rate: Array<number | null> }> = [];
  for (const wc of curves) {
    const oil = wc.streams.find((s) => s.stream === STREAM);
    if (!oil) continue;
    // Argmax over the oil rate, ignoring leading nulls. If a well has
    // no finite peak (all-null history), skip it — it can't anchor.
    const rate = oil.history_rate;
    let peakIdx = -1;
    let peakVal = -Infinity;
    for (let i = 0; i < rate.length; i++) {
      const v = rate[i];
      if (v != null && Number.isFinite(v) && (v as number) > peakVal) {
        peakVal = v as number;
        peakIdx = i;
      }
    }
    if (peakIdx < 0) continue;

    const source = oil[field];
    // For cum, every entry pre-peak is sunk cum we don't want bleeding
    // into the "month-0 starts at zero" convention of the slide chart.
    // Subtract the cum value at the peak so the well's cum trace starts
    // at 0 at peak month and grows from there.
    const baseline =
      field === "history_cum"
        ? (() => {
            const v = source[peakIdx];
            return v != null && Number.isFinite(v) ? (v as number) : 0;
          })()
        : 0;

    const lat = lateralByApi14.get(wc.api14) ?? null;
    // 10k-ft normalization. When lateral is missing, don't scale — the
    // raw trace still informs shape and the reader can see it's an
    // un-normalized line; better than dropping the well entirely.
    const scale = lat && lat > 0 ? 10000 / lat : 1;

    const aligned: Array<number | null> = [];
    for (let i = peakIdx; i < source.length; i++) {
      const v = source[i];
      if (v == null || !Number.isFinite(v)) {
        aligned.push(null);
        continue;
      }
      aligned.push(((v as number) - baseline) * scale);
    }
    out.push({ api14: wc.api14, rate: aligned });
  }
  return out;
}
