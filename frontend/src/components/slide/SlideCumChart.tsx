// Slide-export cum-vs-time panel. Wraps TypeCurveChart with:
//  - linear Y, peak-aligned 0–36 months
//  - oil-only cumulative volume per 10k ft lateral
//  - per-well gray lines from each well's `history_cum`, baselined to
//    its peak month so the trace starts at 0 (matches the type curve's
//    cum, which is the integral of post-peak rate)
//
// Cumulative arrays aren't persisted on the saved type curve — we
// integrate the percentile rate arrays here, matching TypeCurvePage's
// own cum chart math (DAYS_PER_MONTH = 30.4375).

import type { WellCurvesResponse } from "../../api/forecasts";
import type { StreamSeries, TypeCurveRow } from "../../api/typeCurves";

import { TypeCurveChart } from "../../type_curves/TypeCurveChart";
import { buildAlignedWellHistories } from "./slideUtils";

const CUM_X_MONTHS = 36;
const PER_10K_FACTOR = 10;
const DAYS_PER_MONTH = 30.4375;

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

function cumulateNullable(arr: Array<number | null>): Array<number | null> {
  const out: Array<number | null> = [];
  let running = 0;
  let started = false;
  for (const v of arr) {
    if (v == null || !Number.isFinite(v)) {
      out.push(started ? running : null);
      continue;
    }
    running += (v as number) * DAYS_PER_MONTH;
    started = true;
    out.push(running);
  }
  return out;
}

function cumulateAndScale(s: StreamSeries, factor: number): StreamSeries {
  const scC = (arr: Array<number | null>) =>
    cumulateNullable(arr).map((v) =>
      v == null || !Number.isFinite(v) ? v : (v as number) * factor,
    );
  return {
    ...s,
    p10: scC(s.p10),
    p25: scC(s.p25),
    p50: scC(s.p50),
    p75: scC(s.p75),
    p90: scC(s.p90),
    mean: scC(s.mean),
    fitted: s.fitted
      ? (() => {
          const cumRate: number[] = [];
          let running = 0;
          for (const v of s.fitted.smoothed_rate) {
            if (Number.isFinite(v)) {
              running += v * DAYS_PER_MONTH;
            }
            cumRate.push(running * factor);
          }
          return { ...s.fitted, smoothed_rate: cumRate };
        })()
      : null,
  };
}

export function SlideCumChart({
  current,
  previous,
  wellCurves,
  lateralByApi14,
  width = 528,
  height = 240,
}: Props) {
  const currentOil = getOilSeries(current);
  const previousOil = previous ? getOilSeries(previous) : null;
  if (!currentOil) return null;

  const cumScaled = cumulateAndScale(currentOil, PER_10K_FACTOR);
  const cumCompare = previousOil ? cumulateAndScale(previousOil, PER_10K_FACTOR) : null;

  const histories = buildAlignedWellHistories(
    wellCurves,
    lateralByApi14,
    "history_cum",
  );

  return (
    <TypeCurveChart
      series={cumScaled}
      compareSeries={cumCompare}
      compareLabel={previous?.name}
      yAxisType="linear"
      yLabel="Cumulative Oil (BBL / 10k ft)"
      xLabel="Month"
      width={width}
      height={height}
      xMaxMonths={CUM_X_MONTHS}
      wellHistories={histories}
    />
  );
}
