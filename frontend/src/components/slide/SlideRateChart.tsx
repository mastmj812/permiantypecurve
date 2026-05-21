// Slide-export rate-vs-time panel. Wraps TypeCurveChart with:
//  - log Y, peak-aligned (matches the screenshot)
//  - oil-only (the slide is oil-centric; gas/water variants are a v2)
//  - per-well gray histories layered behind P50/mean/fit
//  - both the type-curve series and the per-well lines scaled to
//    per-10,000 ft lateral (saved curves are normalized per-1,000 ft;
//    multiplying by 10 puts everything in the screenshot's
//    "Normalized Oil" magnitude).
//
// The compareSeries (Previous TC) is fed through the same scaling so
// the dotted overlay registers with the rest.

import type { WellCurvesResponse } from "../../api/forecasts";
import type { StreamSeries, TypeCurveRow } from "../../api/typeCurves";

import { TypeCurveChart } from "../../type_curves/TypeCurveChart";
import { buildAlignedWellHistories } from "./slideUtils";

const RATE_X_MONTHS = 36;
const PER_10K_FACTOR = 10; // saved series basis is per-1k-ft

interface Props {
  current: TypeCurveRow;
  previous: TypeCurveRow | null;
  wellCurves: WellCurvesResponse[];
  lateralByApi14: Map<string, number | null>;
  width?: number;
  height?: number;
}

function getOilSeries(curve: TypeCurveRow): StreamSeries | null {
  const streams = (curve.series as { streams?: Record<string, StreamSeries> }).streams;
  return streams?.oil ?? null;
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
  lateralByApi14,
  // Default 5.5" × 2.5" on paper (at 96 px/in). Caller can override
  // via props for one-off layouts.
  width = 528,
  height = 240,
}: Props) {
  const currentOil = getOilSeries(current);
  const previousOil = previous ? getOilSeries(previous) : null;
  if (!currentOil) return null;

  const scaled = scaleSeries(currentOil, PER_10K_FACTOR);
  const scaledCompare = previousOil ? scaleSeries(previousOil, PER_10K_FACTOR) : null;

  const histories = buildAlignedWellHistories(
    wellCurves,
    lateralByApi14,
    "history_rate",
  );

  return (
    <TypeCurveChart
      series={scaled}
      compareSeries={scaledCompare}
      compareLabel={previous?.name}
      yAxisType="log"
      yLabel="Normalized Oil (BOPD / 10k ft)"
      xLabel="Peak Aligned Month"
      width={width}
      height={height}
      xMaxMonths={RATE_X_MONTHS}
      wellHistories={histories}
      yMinFloor={1.0}
    />
  );
}
