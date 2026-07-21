// Type curve chart: shaded P10-P90 band, P25-P75 inner band, P50 line,
// mean line, with a well-count ribbon below.
//
// Sharing visual conventions with DeclineChart (linear vs log y-axis,
// padding, nice ticks) but the data model is different enough to warrant
// its own component.

import { useMemo } from "react";

import type { StreamSeries } from "../api/typeCurves";

export type AxisType = "linear" | "log";

interface Props {
  series: StreamSeries;
  compareSeries?: StreamSeries | null;  // overlay a second curve's P50/mean
  compareLabel?: string;
  yAxisType: AxisType;
  yLabel: string;
  xLabel?: string;
  title?: string;
  width?: number;
  height?: number;
  // When set, clamp the chart's x-domain to the first xMaxMonths months
  // of every series (bands, P50, mean, well-count, fitted overlay). Used
  // by the early-time inspection chart on TypeCurvePage to zoom into
  // the ramp + peak + early decline.
  xMaxMonths?: number;
  // When set, generate x-axis ticks every `xTickStep` months instead of
  // the default nice-tick spacing. The early-time chart uses 1 so every
  // month gets a labelled vertical gridline.
  xTickStep?: number;
  // When non-null, this array replaces series.fitted.smoothed_rate as
  // the bold-slate "fitted type curve" path. Used by the manual-tweak
  // panel to overlay the tweaked curve without mutating the underlying
  // series prop. Length should match series.p50; xMaxMonths slicing is
  // applied here too.
  smoothedOverride?: number[] | null;
  // Per-well rate histories rendered as thin gray polylines behind the
  // bands. Drives the slide-export "rep wells" overlay — one faint line
  // per included well, peak-aligned, lateral-normalized by the caller.
  // Index 0 of each `rate` array maps to month 0 on the x-axis; nulls
  // break the polyline. Contributes to y-axis range so an outlier well
  // doesn't escape the plot area.
  wellHistories?: Array<{ api10: string; rate: Array<number | null> }> | null;
  // When set on a log Y axis, clamp the visible y-min to this value
  // (e.g. yMinFloor=1.0 stops the slide rate chart from extending the
  // axis down into single-digit decimals where a few late-life tails
  // would otherwise compress the readable portion of the curve).
  yMinFloor?: number;
  // Slide-export mode: tighter inner padding and the well-count
  // ribbon is hidden, so the plot area maximizes within the 5.42" ×
  // 2.16" picture box. In-app charts on TypeCurvePage leave this
  // false and keep the ribbon + roomier margins.
  compactLayout?: boolean;
}

const DEFAULT_PAD = { top: 26, right: 18, bottom: 48, left: 60 };
// Slide-export padding. Left fits the rotated y-axis caption at x=14
// + y-tick labels right-aligned at PAD.left-6=44 (max ~24px wide for
// "1.0M" / "1.5k"). Right is just enough that the rightmost x-tick
// label centered at xScale(35) doesn't clip the SVG edge.
const COMPACT_PAD = { top: 8, right: 4, bottom: 32, left: 50 };
const DEFAULT_RIBBON_H = 38; // well-count strip below the main panel
const COMPACT_RIBBON_H = 0; // hidden in slide-export mode
const DEFAULT_RIBBON_GAP = 6; // gap between plot and ribbon

export function TypeCurveChart({
  series,
  compareSeries,
  compareLabel,
  yAxisType,
  yLabel,
  xLabel = "Months since peak",
  title,
  width = 520,
  height = 320,
  xMaxMonths,
  xTickStep,
  smoothedOverride,
  wellHistories,
  yMinFloor,
  compactLayout = false,
}: Props) {
  const PAD = compactLayout ? COMPACT_PAD : DEFAULT_PAD;
  const RIBBON_H = compactLayout ? COMPACT_RIBBON_H : DEFAULT_RIBBON_H;
  const RIBBON_GAP = RIBBON_H > 0 ? DEFAULT_RIBBON_GAP : 0;
  // Slice every series to the first xMaxMonths if provided. This is the
  // mechanism the early-time inspection chart uses to zoom into the
  // ramp + peak + early decline without forking the rendering logic.
  const displaySeries = useMemo(
    () => (xMaxMonths != null ? sliceSeries(series, xMaxMonths) : series),
    [series, xMaxMonths],
  );
  const displayCompare = useMemo(
    () =>
      compareSeries && xMaxMonths != null
        ? sliceSeries(compareSeries, xMaxMonths)
        : compareSeries ?? null,
    [compareSeries, xMaxMonths],
  );
  // Use both curves to size the y-axis when comparing.
  const seriesForAxes = useMemo(
    () =>
      displayCompare ? mergeSeriesForAxes(displaySeries, displayCompare) : displaySeries,
    [displaySeries, displayCompare],
  );
  // Slice per-well histories to the same x-domain. Pulled into its own
  // memo so we don't recompute on every prop change.
  const displayWells = useMemo(() => {
    if (!wellHistories || wellHistories.length === 0) return null;
    const lim = xMaxMonths != null ? Math.max(1, xMaxMonths) : null;
    return wellHistories.map((w) => ({
      api10: w.api10,
      rate: lim != null ? w.rate.slice(0, lim) : w.rate,
    }));
  }, [wellHistories, xMaxMonths]);
  const { mainArea, ribbonArea, xScale, yScale, yTicks, xTicks, countScale } =
    useMemo(
      () =>
        computeAxes(
          seriesForAxes,
          yAxisType,
          width,
          height,
          xTickStep,
          displayWells,
          yMinFloor,
          PAD,
          RIBBON_H,
          RIBBON_GAP,
        ),
      [
        seriesForAxes, yAxisType, width, height, xTickStep, displayWells,
        yMinFloor, PAD, RIBBON_H, RIBBON_GAP,
      ],
    );

  const bandOuter = bandPath(displaySeries.p10, displaySeries.p90, xScale, yScale, yAxisType);
  const bandInner = bandPath(displaySeries.p25, displaySeries.p75, xScale, yScale, yAxisType);
  const p50Path = linePath(displaySeries.p50, xScale, yScale, yAxisType);
  const meanPath = linePath(displaySeries.mean, xScale, yScale, yAxisType);
  const countPath = countRibbonPath(displaySeries.well_count, xScale, countScale);
  const comparePath = displayCompare
    ? linePath(displayCompare.p50, xScale, yScale, yAxisType)
    : null;
  // Fitted Arps overlay on the P50 — the "actual type curve" the
  // engineer will hand to the economics model. When the user is
  // tweaking, smoothedOverride takes priority over the auto-fit.
  const fittedSmoothed: Array<number | null> | null = smoothedOverride
    ? (xMaxMonths != null ? smoothedOverride.slice(0, xMaxMonths) : smoothedOverride)
    : displaySeries.fitted
      ? displaySeries.fitted.smoothed_rate
      : null;
  const fittedPath = fittedSmoothed
    ? linePath(fittedSmoothed, xScale, yScale, yAxisType)
    : null;

  return (
    <svg width={width} height={height} className="type-curve-chart">
      {/* Main plot frame */}
      <rect
        x={mainArea.x}
        y={mainArea.y}
        width={mainArea.w}
        height={mainArea.h}
        fill="#fff"
        stroke="#e5e7eb"
      />

      {/* Y gridlines + ticks */}
      {yTicks.map((t) => (
        <g key={`y${t.label}`}>
          <line
            x1={mainArea.x}
            x2={mainArea.x + mainArea.w}
            y1={yScale(t.value)}
            y2={yScale(t.value)}
            stroke="#f3f4f6"
          />
          <text
            x={mainArea.x - 6}
            y={yScale(t.value)}
            textAnchor="end"
            dominantBaseline="middle"
            fontSize="10"
            fill="#6b7280"
          >
            {t.label}
          </text>
        </g>
      ))}

      {/* X gridlines + ticks. Labels sit below the well-count ribbon so
          the ribbon's fill doesn't cover them. */}
      {xTicks.map((t) => (
        <g key={`x${t}`}>
          <line
            x1={xScale(t)}
            x2={xScale(t)}
            y1={mainArea.y}
            y2={mainArea.y + mainArea.h}
            stroke="#f3f4f6"
          />
          <text
            x={xScale(t)}
            y={ribbonArea.y + ribbonArea.h + 12}
            textAnchor="middle"
            fontSize="10"
            fill="#6b7280"
          >
            {t}
          </text>
        </g>
      ))}

      {/* Per-well histories — thin gray polylines, sit behind the
          bands so the aggregates and fit dominate the visual hierarchy.
          Opacity is per-line so a 20-well cohort doesn't crowd the
          foreground. */}
      {displayWells &&
        displayWells.map((w) => {
          const d = linePath(w.rate, xScale, yScale, yAxisType);
          if (!d) return null;
          return (
            <path
              key={`hist-${w.api10}`}
              d={d}
              fill="none"
              stroke="#4b5563"
              strokeWidth={0.9}
              strokeOpacity={0.65}
            />
          );
        })}

      {/* Band outer P10-P90 */}
      {bandOuter && (
        <path d={bandOuter} fill="#bfdbfe" fillOpacity={0.55} stroke="none" />
      )}
      {/* Band inner P25-P75 */}
      {bandInner && (
        <path d={bandInner} fill="#60a5fa" fillOpacity={0.45} stroke="none" />
      )}
      {/* Raw P50 — the cross-well median per month. THIS is the
          right visual fit target: for lognormal-distributed well
          rates (typical Permian) the median is robust to high-flow
          outliers and tracks the operator-convention P50 reserves.
          Drawn solid + saturated so it draws the eye. */}
      {p50Path && (
        <path
          d={p50Path}
          fill="none"
          stroke="#1d4ed8"
          strokeWidth={2.25}
        />
      )}
      {/* Mean line — informational only. Right-skewed cohort rate
          distributions pull the mean above the median, so fitting
          visually to this line builds an optimistic TC. Rendered as
          a quiet gray dashed to discourage that. */}
      {meanPath && (
        <path
          d={meanPath}
          fill="none"
          stroke="#9ca3af"
          strokeWidth={1.0}
          strokeDasharray="2 4"
        />
      )}
      {/* Fitted Arps overlay — the actual type curve. Bold dark line so
          it dominates the visual hierarchy; everything else is context. */}
      {fittedPath && (
        <path
          d={fittedPath}
          fill="none"
          stroke="#0f172a"
          strokeWidth={2.5}
        />
      )}
      {/* Comparison P50 from a second curve (Library "compare with") */}
      {comparePath && (
        <path
          d={comparePath}
          fill="none"
          stroke="#16a34a"
          strokeWidth={1.5}
        />
      )}
      {compareSeries && compareLabel && (
        <g>
          <line
            x1={mainArea.x + 8}
            x2={mainArea.x + 24}
            y1={mainArea.y + 12}
            y2={mainArea.y + 12}
            stroke="#16a34a"
            strokeWidth={2}
          />
          <text x={mainArea.x + 28} y={mainArea.y + 15} fontSize="10" fill="#374151">
            vs {compareLabel}
          </text>
        </g>
      )}

      {/* Ribbon: well_count. Hidden when compactLayout=true (slide-
          export charts) so the plot maximizes its picture-box room. */}
      {RIBBON_H > 0 && (
        <>
          <rect
            x={ribbonArea.x}
            y={ribbonArea.y}
            width={ribbonArea.w}
            height={ribbonArea.h}
            fill="#f9fafb"
            stroke="#e5e7eb"
          />
          {countPath && <path d={countPath} fill="none" stroke="#6b7280" strokeWidth={1} />}
          <text
            x={ribbonArea.x - 6}
            y={ribbonArea.y + ribbonArea.h / 2}
            textAnchor="end"
            dominantBaseline="middle"
            fontSize="9"
            fill="#6b7280"
          >
            well count
          </text>
          {/* Min/max labels on the ribbon scale */}
          <text
            x={ribbonArea.x + ribbonArea.w + 2}
            y={ribbonArea.y + 8}
            fontSize="9"
            fill="#6b7280"
          >
            {Math.max(...series.well_count)}
          </text>
          <text
            x={ribbonArea.x + ribbonArea.w + 2}
            y={ribbonArea.y + ribbonArea.h - 2}
            fontSize="9"
            fill="#6b7280"
          >
            0
          </text>
        </>
      )}

      {/* Optional title above the plot */}
      {title && (
        <text
          x={mainArea.x + mainArea.w / 2}
          y={6}
          dominantBaseline="hanging"
          textAnchor="middle"
          fontSize="12"
          fontWeight="600"
          fill="#1f2937"
        >
          {title}
        </text>
      )}

      {/* Axis labels */}
      <text
        x={mainArea.x + mainArea.w / 2}
        y={height - 4}
        textAnchor="middle"
        fontSize="11"
        fill="#374151"
      >
        {xLabel}
      </text>
      <text
        x={14}
        y={mainArea.y + mainArea.h / 2}
        transform={`rotate(-90 14 ${mainArea.y + mainArea.h / 2})`}
        textAnchor="middle"
        fontSize="11"
        fill="#374151"
      >
        {yLabel}
        {yAxisType === "log" ? " (log)" : ""}
      </text>
    </svg>
  );
}

// ---------- helpers ----------

function computeAxes(
  series: StreamSeries,
  axis: AxisType,
  width: number,
  height: number,
  xTickStep?: number,
  wellHistories?: Array<{ api10: string; rate: Array<number | null> }> | null,
  yMinFloor?: number,
  PAD: { top: number; right: number; bottom: number; left: number } = DEFAULT_PAD,
  RIBBON_H: number = DEFAULT_RIBBON_H,
  RIBBON_GAP: number = DEFAULT_RIBBON_GAP,
) {
  const mainH = height - PAD.top - PAD.bottom - RIBBON_H - RIBBON_GAP;
  const mainArea = { x: PAD.left, y: PAD.top, w: width - PAD.left - PAD.right, h: mainH };
  const ribbonArea = {
    x: PAD.left,
    y: PAD.top + mainH + RIBBON_GAP,
    w: width - PAD.left - PAD.right,
    h: RIBBON_H,
  };
  // x-domain reaches as far as the longest input — series or any well
  // history. Keeps a 36-month-history well visible on a 24-month chart.
  let months = series.p50.length;
  if (wellHistories) {
    for (const w of wellHistories) months = Math.max(months, w.rate.length);
  }

  const xMax = Math.max(months - 1, 1);
  const xScale = (i: number) =>
    mainArea.x + (i / xMax) * mainArea.w;

  // y range: pull from any percentile that's defined, plus every
  // per-well rate value so an outlier well doesn't escape the plot area.
  const flat: number[] = [];
  for (const arr of [series.p10, series.p25, series.p50, series.p75, series.p90, series.mean]) {
    for (const v of arr) if (v != null && Number.isFinite(v)) flat.push(v);
  }
  if (wellHistories) {
    for (const w of wellHistories) {
      for (const v of w.rate) if (v != null && Number.isFinite(v)) flat.push(v);
    }
  }
  const xTicks =
    xTickStep != null && xTickStep > 0
      ? stepTicks(0, xMax, xTickStep)
      : niceTicks(0, xMax, 6);
  if (flat.length === 0) {
    return {
      mainArea, ribbonArea,
      xScale,
      yScale: () => mainArea.y + mainArea.h,
      yTicks: [],
      xTicks,
      countScale: () => ribbonArea.y + ribbonArea.h,
    };
  }
  // yMinFloor pins the y-axis bottom (log only) at a fixed value
  // regardless of the data min — the slide rate chart sets this to
  // 1.0 so the visible band stays at "type-curve magnitudes" rather
  // than zooming in on a couple of single-digit late-life tails.
  // Without the floor we fall back to data-driven min, clamped at
  // 1e-3 so a stray zero doesn't blow up Math.log10.
  const yMin =
    axis === "log"
      ? yMinFloor != null && yMinFloor > 0
        ? yMinFloor
        : Math.max(1e-3, Math.min(...flat))
      : 0;
  const yMax = Math.max(...flat);
  const yScale = (y: number) => {
    if (axis === "log") {
      const ly = Math.log10(Math.max(y, yMin));
      const lyMin = Math.log10(yMin);
      const lyMax = Math.log10(yMax);
      const r = (ly - lyMin) / Math.max(lyMax - lyMin, 1e-9);
      return mainArea.y + mainArea.h * (1 - r);
    }
    const r = (y - yMin) / Math.max(yMax - yMin, 1e-9);
    return mainArea.y + mainArea.h * (1 - r);
  };

  const yTicks =
    axis === "log"
      ? logTicks(yMin, yMax)
      : niceTicks(yMin, yMax, 5).map((v) => ({ value: v, label: fmt(v) }));

  const countMax = Math.max(1, ...series.well_count);
  const countScale = (n: number) =>
    ribbonArea.y + ribbonArea.h - (n / countMax) * (ribbonArea.h - 4);

  return { mainArea, ribbonArea, xScale, yScale, yTicks, xTicks, countScale };
}

function stepTicks(min: number, max: number, step: number): number[] {
  const out: number[] = [];
  for (let v = min; v <= max + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

function sliceSeries(s: StreamSeries, n: number): StreamSeries {
  // Returns a shallow copy with every per-month array truncated to the
  // first `n` entries. Used by the early-time inspection chart. We
  // truncate the fitted overlay's smoothed_rate too so the bold-slate
  // line ends at the same x-domain as the rest of the series.
  const lim = Math.max(1, n);
  return {
    ...s,
    p10: s.p10.slice(0, lim),
    p25: s.p25.slice(0, lim),
    p50: s.p50.slice(0, lim),
    p75: s.p75.slice(0, lim),
    p90: s.p90.slice(0, lim),
    mean: s.mean.slice(0, lim),
    well_count: s.well_count.slice(0, lim),
    fitted: s.fitted
      ? { ...s.fitted, smoothed_rate: s.fitted.smoothed_rate.slice(0, lim) }
      : null,
  };
}

function mergeSeriesForAxes(a: StreamSeries, b: StreamSeries): StreamSeries {
  // For axis sizing only — pick the longer x-range and the higher y-max.
  // We pad the shorter series with nulls to match length, then concat NOT
  // along the time axis but as extra "samples" the y-axis sees. The
  // primary `series` we draw paths from is unchanged; this combined one
  // is only used inside computeAxes for y-range determination.
  const n = Math.max(a.p50.length, b.p50.length);
  const pad = <T,>(arr: T[], fill: T): T[] => {
    if (arr.length >= n) return arr.slice(0, n);
    return [...arr, ...Array<T>(n - arr.length).fill(fill)];
  };
  // x-axis length comes from `n`; y-axis uses pointwise max of both.
  const ymax = <T extends number | null>(x: T, y: T): T => {
    if (x == null) return y;
    if (y == null) return x;
    return x >= y ? x : y;
  };
  const ymin = <T extends number | null>(x: T, y: T): T => {
    if (x == null) return y;
    if (y == null) return x;
    return x <= y ? x : y;
  };
  const A = {
    p10: pad(a.p10, null as number | null), p25: pad(a.p25, null as number | null),
    p50: pad(a.p50, null as number | null), p75: pad(a.p75, null as number | null),
    p90: pad(a.p90, null as number | null), mean: pad(a.mean, null as number | null),
    well_count: pad(a.well_count, 0),
  };
  const B = {
    p10: pad(b.p10, null as number | null), p25: pad(b.p25, null as number | null),
    p50: pad(b.p50, null as number | null), p75: pad(b.p75, null as number | null),
    p90: pad(b.p90, null as number | null), mean: pad(b.mean, null as number | null),
    well_count: pad(b.well_count, 0),
  };
  return {
    // Take the broader band envelope from either curve so the axis covers
    // both. SPE orientation: p10/p25 are the UPPER band edges (high case)
    // → pointwise max; p75/p90 are the LOWER edges → pointwise min.
    p10: A.p10.map((v, i) => ymax(v, B.p10[i] ?? null)),
    p25: A.p25.map((v, i) => ymax(v, B.p25[i] ?? null)),
    p50: A.p50.map((v, i) => ymax(v, B.p50[i] ?? null)),
    p75: A.p75.map((v, i) => ymin(v, B.p75[i] ?? null)),
    p90: A.p90.map((v, i) => ymin(v, B.p90[i] ?? null)),
    mean: A.mean.map((v, i) => ymax(v, B.mean[i] ?? null)),
    well_count: A.well_count.map((v, i) => Math.max(v, B.well_count[i] ?? 0)),
    implied_eur_per_1000ft: a.implied_eur_per_1000ft,
    // fitted + fitted_eur_per_unit + fitted_per_percentile are
    // per-curve and not part of axis-range logic; the merged shape
    // just needs *something* of the right type. Drop all fits here.
    fitted: null,
    fitted_eur_per_unit: null,
    fitted_per_percentile: null,
  };
}

function bandPath(
  low: Array<number | null>,
  high: Array<number | null>,
  xScale: (i: number) => number,
  yScale: (y: number) => number,
  axis: AxisType,
): string | null {
  const lows: [number, number][] = [];
  const highs: [number, number][] = [];
  for (let i = 0; i < low.length; i++) {
    const lo = low[i]!;
    const hi = high[i]!;
    if (lo == null || hi == null) continue;
    if (axis === "log" && (lo <= 0 || hi <= 0)) continue;
    lows.push([xScale(i), yScale(lo)]);
    highs.push([xScale(i), yScale(hi)]);
  }
  if (lows.length < 2) return null;
  const downward = lows.map(([x, y], idx) =>
    `${idx === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`,
  );
  const upward = [...highs].reverse().map(([x, y]) =>
    `L${x.toFixed(2)},${y.toFixed(2)}`,
  );
  return `${downward.join(" ")} ${upward.join(" ")} Z`;
}

function linePath(
  values: Array<number | null>,
  xScale: (i: number) => number,
  yScale: (y: number) => number,
  axis: AxisType,
): string | null {
  const segs: string[] = [];
  let started = false;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null || !Number.isFinite(v) || (axis === "log" && v <= 0)) {
      started = false;
      continue;
    }
    segs.push(`${started ? "L" : "M"}${xScale(i).toFixed(2)},${yScale(v).toFixed(2)}`);
    started = true;
  }
  return segs.length > 0 ? segs.join(" ") : null;
}

function countRibbonPath(
  counts: number[],
  xScale: (i: number) => number,
  yScale: (n: number) => number,
): string | null {
  if (counts.length === 0) return null;
  return counts
    .map((n, i) => `${i === 0 ? "M" : "L"}${xScale(i).toFixed(2)},${yScale(n).toFixed(2)}`)
    .join(" ");
}

function niceTicks(min: number, max: number, count: number): number[] {
  if (min === max) return [min];
  const step = niceStep((max - min) / count);
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

function logTicks(min: number, max: number): { value: number; label: string }[] {
  const lo = Math.floor(Math.log10(Math.max(min, 1e-3)));
  const hi = Math.ceil(Math.log10(Math.max(max, 1e-2)));
  const out: { value: number; label: string }[] = [];
  for (let p = lo; p <= hi; p++) {
    const v = Math.pow(10, p);
    out.push({ value: v, label: fmt(v) });
  }
  return out;
}

function niceStep(rough: number): number {
  if (rough <= 0) return 1;
  const exp = Math.floor(Math.log10(rough));
  const frac = rough / Math.pow(10, exp);
  const mult = frac < 1.5 ? 1 : frac < 3 ? 2 : frac < 7 ? 5 : 10;
  return mult * Math.pow(10, exp);
}

function fmt(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  if (v >= 10) return v.toFixed(0);
  if (v >= 1) return v.toFixed(1);
  return v.toPrecision(2);
}
