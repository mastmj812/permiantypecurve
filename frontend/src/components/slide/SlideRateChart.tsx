// Slide-export rate-vs-time panel. Wraps TypeCurveChart with:
//  - log Y, peak-aligned to the cohort's oil-peak t=0
//  - per-stream variant (oil / gas / water) — title, units, and series
//    swap based on the `stream` prop; alignment + history overlay still
//    anchor on oil rate so the three slides share t=0
//  - per-well gray histories layered behind P50/mean/fit
//  - rates scaled per-10,000 ft lateral (saved curves are per-1k-ft;
//    multiplying by 10 puts values in the "Normalized" magnitude the
//    engineering team reads)

import type { Stream, WellCurvesResponse } from "../../api/forecasts";
import type { StreamSeries, TypeCurveRow } from "../../api/typeCurves";

import { TypeCurveChart } from "../../type_curves/TypeCurveChart";
import { buildAlignedWellHistories } from "./slideUtils";

const RATE_X_MONTHS = 36;
const PER_10K_FACTOR = 10; // saved series basis is per-1k-ft

interface Props {
  current: TypeCurveRow;
  previous: TypeCurveRow | null;
  wellCurves: WellCurvesResponse[];
  lateralByApi10: Map<string, number | null>;
  stream?: Stream;
  width?: number;
  height?: number;
}

// y-axis label per stream — units match the calday-rate convention
// the backend stores (BOPD for oil, MCFD for gas, BWPD for water).
const Y_LABEL_BY_STREAM: Record<Stream, string> = {
  oil: "Normalized Oil (BOPD / 10k ft)",
  gas: "Normalized Gas (MCFD / 10k ft)",
  water: "Normalized Water (BWPD / 10k ft)",
};

function getStreamSeries(curve: TypeCurveRow, stream: Stream): StreamSeries | null {
  const streams = (curve.series as { streams?: Record<string, StreamSeries> }).streams;
  return streams?.[stream] ?? null;
}

// Scale every rate field by `factor` so the displayed chart is in
// per-10kft units. New objects all the way down — we never mutate
// the saved-curve series.
function scaleSeries(s: StreamSeries, factor: number): StreamSeries {
  const sc = (arr: Array<number | null>) =>
    arr.map((v) => (v == null || !Number.isFinite(v) ? v : (v as number) * factor));
  return {
    ...s,
    p10: sc(s.p10),
    p25: sc(s.p25),
    p50: sc(s.p50),
    p75: sc(s.p75),
    p90: sc(s.p90),
    mean: sc(s.mean),
    fitted: s.fitted
      ? {
          ...s.fitted,
          smoothed_rate: s.fitted.smoothed_rate.map((v) =>
            Number.isFinite(v) ? v * factor : v,
          ),
        }
      : null,
  };
}

export function SlideRateChart({
  current,
  previous,
  wellCurves,
  lateralByApi10,
  stream = "oil",
  // 5.42" × 2.16" at 96 px/in — the user-specified PowerPoint chart
  // dimensions. The export places each chart as a SEPARATE picture
  // on the slide at exactly these inches, so the preview and the
  // exported deck render the chart at the same physical size.
  width = 520,
  height = 207,
}: Props) {
  const currentStream = getStreamSeries(current, stream);
  const previousStream = previous ? getStreamSeries(previous, stream) : null;
  if (!currentStream) return null;

  const scaled = scaleSeries(currentStream, PER_10K_FACTOR);
  const scaledCompare = previousStream
    ? scaleSeries(previousStream, PER_10K_FACTOR)
    : null;

  const histories = buildAlignedWellHistories(
    wellCurves,
    lateralByApi10,
    "history_rate",
    stream,
  );

  return (
    <TypeCurveChart
      series={scaled}
      compareSeries={scaledCompare}
      compareLabel={previous?.name}
      yAxisType="log"
      yLabel={Y_LABEL_BY_STREAM[stream]}
      xLabel="Peak Aligned Month"
      width={width}
      height={height}
      xMaxMonths={RATE_X_MONTHS}
      wellHistories={histories}
      yMinFloor={100}
      compactLayout
    />
  );
}
