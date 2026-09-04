// InspectProductionCharts — paired rate-vs-time + cum-vs-time overlay
// charts for the staged wells inside the inspect modal. All wells on
// one set of axes, first-prod-aligned (x = months since first prod),
// colored to match the gun-barrel circles (by formation).
//
// Toggle: plot mode (oil/gas/water streams, plus GOR/WOR ratios),
// normalize-per-10kft, and the x-axis basis (months since first prod
// vs calendar date). The user asked for per-10,000-ft lateral as the
// default — the documented trap is that raw rates make longer laterals
// look like better wells when they're really just longer. Ratio modes
// ignore (and hide) the normalize toggle: lateral length cancels.
// Date mode positions each point at its OWN prod month (from the
// curves bundle's `months` dates), so wells plot on a shared calendar
// axis and any months missing a production row show as real gaps
// instead of silently compressing the index axis.

import { useEffect, useId, useMemo, useState } from "react";

import type { WellDetailLite } from "../api/wells";
import {
  fetchWellCurves,
  type Stream,
  type WellCurvesResponse,
} from "../api/forecasts";
import { colorForFormation } from "../map/formations";
import { COHORT_HALO_COLOR } from "../map/wellsLayers";

export interface InspectProductionChartsProps {
  api10s: string[];
  // Per-well metadata keyed by api10 — supplies the formation color
  // and the lateral_ft used by the /10kft normalization. Comes from
  // the same fetchWellDetails() call that feeds the gun-barrel.
  wellsByApi10: Map<string, WellDetailLite>;
  // Deselected wells (api10s NOT in this set) render as ghosted lines
  // — matches the gun-barrel's dimmed-circle convention so the two
  // views agree on what "unselected" looks like. Optional so this
  // component still renders standalone outside the inspect modal.
  selectedApi10s?: Set<string>;
  // api10s ALREADY in the active cohort — each gets a sky-blue halo
  // stroke under its line, mirroring the gun-barrel circle halo and the
  // map's cohort sticks. Empty/undefined outside the modal.
  cohortApi10s?: Set<string>;
  // Lifted hover state — when set, the matching polyline bolds up
  // (and the gun-barrel circle bolds too via the same prop on the
  // sibling component).
  hoveredApi10?: string | null;
  onHover?: (api10: string | null) => void;
  // Per-chart pixel size, driven by the (resizable) inspect modal.
  // Omitted → the OverlayChart defaults (430×240).
  chartWidth?: number;
  chartHeight?: number;
}

const PAD = { top: 26, right: 18, bottom: 42, left: 64 };

// Plot modes: the three raw streams plus the two oil-denominator
// ratios. GOR/WOR are derived client-side from the same per-well
// curves bundle — no extra fetch.
type PlotMode = Stream | "gor" | "wor";
const PLOT_MODES: PlotMode[] = ["oil", "gas", "water", "gor", "wor"];

// Ratio of two calendar-day rates. Both rates share the same day-count
// denominator, so this collapses to the monthly volume ratio — downtime
// cancels instead of distorting the ratio. Null when oil is zero/absent
// (shut-in months) so the polyline breaks rather than spiking.
function streamRatio(
  num: number | null | undefined,
  denom: number | null,
  scale: number,
): number | null {
  if (num == null || denom == null || denom <= 0) return null;
  return (num * scale) / denom;
}

// Absolute month index (year*12 + month-1) of an ISO "YYYY-MM-DD"
// prod-date string. String math on purpose: `new Date("2021-07-01")`
// parses as UTC midnight and can shift a month under a western-
// hemisphere local timezone.
function monthIndex(iso: string): number {
  return Number(iso.slice(0, 4)) * 12 + (Number(iso.slice(5, 7)) - 1);
}

type XAxisMode = "months" | "date";

interface WellSeries {
  api10: string;
  formation: string | null;
  // Aligned to "months since first prod" — index = X position (months).
  // null entries preserve the gaps (downtime) that history_rate may
  // have, so the polyline breaks cleanly across them.
  rate: Array<number | null>;
  cum: Array<number | null>;
  // Absolute month index per point (same length as rate/cum) — the X
  // position in date mode. From the curves bundle's own `months`
  // dates, so a well needs no separate first-prod lookup.
  monthIdx: number[];
}

export function InspectProductionCharts({
  api10s,
  wellsByApi10,
  selectedApi10s,
  cohortApi10s,
  hoveredApi10 = null,
  onHover,
  chartWidth,
  chartHeight,
}: InspectProductionChartsProps) {
  const [mode, setMode] = useState<PlotMode>("oil");
  const [normalize, setNormalize] = useState<boolean>(true);
  const [xAxisMode, setXAxisMode] = useState<XAxisMode>("months");
  const [curves, setCurves] = useState<
    Map<string, WellCurvesResponse> | null
  >(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Fan out curves fetch on api10 set change. Promise.allSettled so a
  // single 404 doesn't blank the whole modal — wells without
  // production show up as "no production data".
  useEffect(() => {
    let cancelled = false;
    if (api10s.length === 0) {
      setCurves(new Map());
      return () => {
        cancelled = true;
      };
    }
    setLoading(true);
    setError(null);
    Promise.allSettled(api10s.map((a) => fetchWellCurves(a)))
      .then((results) => {
        if (cancelled) return;
        const out = new Map<string, WellCurvesResponse>();
        for (let i = 0; i < results.length; i++) {
          const r = results[i]!;
          if (r.status === "fulfilled") {
            out.set(api10s[i]!, r.value);
          }
        }
        setCurves(out);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api10s]);

  // Build per-well aligned series for the active mode + normalize
  // setting. Memoized so toggling normalize doesn't re-fetch.
  const series = useMemo<WellSeries[]>(() => {
    if (!curves) return [];
    const out: WellSeries[] = [];
    for (const api10 of api10s) {
      const resp = curves.get(api10);
      if (!resp) continue;
      const meta = wellsByApi10.get(api10) ?? null;

      let rate: Array<number | null>;
      let cum: Array<number | null>;
      let monthDates: string[];
      if (mode === "gor" || mode === "wor") {
        // Index-wise zip is safe: the backend builds all three streams
        // from the same prod_rows loop, so `months` is identical across
        // streams for a given well. GOR ×1000 converts MCF/BBL → SCF/BBL
        // (the conventional unit); WOR is dimensionless BBL/BBL.
        const oil = resp.streams.find((s) => s.stream === "oil");
        const num = resp.streams.find(
          (s) => s.stream === (mode === "gor" ? "gas" : "water"),
        );
        if (!oil || !num) continue;
        const scale = mode === "gor" ? 1000 : 1;
        rate = oil.history_rate.map((o, i) =>
          streamRatio(num.history_rate[i], o, scale),
        );
        // Cum ratio = producing GOR/WOR to date (cum gas over cum oil),
        // not a running integral of the monthly ratio.
        cum = oil.history_cum.map((o, i) =>
          streamRatio(num.history_cum[i], o, scale),
        );
        monthDates = oil.months;
      } else {
        const sc = resp.streams.find((s) => s.stream === mode);
        if (!sc) continue;
        const lateralFt = meta?.lateral_ft ?? null;
        // Normalize per 10kft when requested AND lateral is known.
        // Unknown-lateral wells fall back to raw so a missing length
        // doesn't yank them off the chart (an open question the user
        // didn't litigate; raw is the conservative default).
        const norm =
          normalize && lateralFt && lateralFt > 0 ? 10_000 / lateralFt : 1;
        rate = sc.history_rate.map((v) => (v == null ? null : v * norm));
        cum = sc.history_cum.map((v) => (v == null ? null : v * norm));
        monthDates = sc.months;
      }

      out.push({
        api10,
        // formation_blueox code drives the series color (colorForFormation
        // resolves codes); raw formation stays available on the bundle.
        formation: meta?.formation_blueox ?? null,
        rate,
        cum,
        monthIdx: monthDates.map(monthIndex),
      });
    }
    return out;
  }, [curves, api10s, mode, normalize, wellsByApi10]);

  // Show the overlay for any well that has non-null production. A single
  // well renders its own rate/cum curve (no comparison, but still a valid
  // QC read); zero wells-with-history is the only case we hide. Caller
  // can still render whatever headers/footers it wants around us.
  const wellsWithData = series.filter((s) =>
    s.rate.some((v) => v != null && v > 0),
  );
  const showCharts = wellsWithData.length >= 1;

  if (loading) {
    return <div className="inspect-charts-empty">Loading production…</div>;
  }
  if (error) {
    return <div className="inspect-charts-empty">Failed to load: {error}</div>;
  }
  if (!showCharts) {
    return (
      <div className="inspect-charts-empty">
        Production overlay hidden — no wells with history.
      </div>
    );
  }

  const isRatio = mode === "gor" || mode === "wor";
  const ratioUnits = mode === "gor" ? "scf/bbl" : "bbl/bbl";

  return (
    <div className="inspect-charts">
      <div className="inspect-charts-controls">
        <div className="inspect-charts-control-group">
          <span className="inspect-charts-control-label">Stream</span>
          {PLOT_MODES.map((m) => (
            <button
              key={m}
              type="button"
              className={`inspect-charts-pill ${
                mode === m ? "is-active" : ""
              }`}
              onClick={() => setMode(m)}
            >
              {m === "gor" || m === "wor" ? m.toUpperCase() : m}
            </button>
          ))}
        </div>
        {!isRatio && (
          <div className="inspect-charts-control-group">
            <span className="inspect-charts-control-label">Scale</span>
            <button
              type="button"
              className={`inspect-charts-pill ${normalize ? "is-active" : ""}`}
              onClick={() => setNormalize(true)}
            >
              per 10,000 ft
            </button>
            <button
              type="button"
              className={`inspect-charts-pill ${!normalize ? "is-active" : ""}`}
              onClick={() => setNormalize(false)}
            >
              raw
            </button>
          </div>
        )}
        <div className="inspect-charts-control-group">
          <span className="inspect-charts-control-label">X axis</span>
          <button
            type="button"
            className={`inspect-charts-pill ${
              xAxisMode === "months" ? "is-active" : ""
            }`}
            onClick={() => setXAxisMode("months")}
          >
            months since first prod
          </button>
          <button
            type="button"
            className={`inspect-charts-pill ${
              xAxisMode === "date" ? "is-active" : ""
            }`}
            onClick={() => setXAxisMode("date")}
          >
            date
          </button>
        </div>
      </div>

      <div className="inspect-charts-row">
        <OverlayChart
          title={
            isRatio
              ? `${mode.toUpperCase()} vs. ${xAxisMode === "date" ? "date" : "months from first prod"}`
              : `${mode.toUpperCase()} rate vs. ${xAxisMode === "date" ? "date" : "months from first prod"}`
          }
          yLabel={
            isRatio
              ? `${mode.toUpperCase()} (${ratioUnits})`
              : normalize
                ? "rate / 10kft"
                : "rate"
          }
          series={wellsWithData}
          accessor={(s) => s.rate}
          xMode={xAxisMode}
          robustScale={isRatio}
          selectedApi10s={selectedApi10s}
          cohortApi10s={cohortApi10s}
          hoveredApi10={hoveredApi10}
          onHover={onHover}
          width={chartWidth}
          height={chartHeight}
        />
        <OverlayChart
          title={
            isRatio
              ? `Cum ${mode.toUpperCase()} vs. ${xAxisMode === "date" ? "date" : "months from first prod"}`
              : `${mode.toUpperCase()} cum vs. ${xAxisMode === "date" ? "date" : "months from first prod"}`
          }
          yLabel={
            isRatio
              ? `cum ${mode.toUpperCase()} (${ratioUnits})`
              : normalize
                ? "cum / 10kft"
                : "cum"
          }
          series={wellsWithData}
          accessor={(s) => s.cum}
          xMode={xAxisMode}
          robustScale={isRatio}
          selectedApi10s={selectedApi10s}
          cohortApi10s={cohortApi10s}
          hoveredApi10={hoveredApi10}
          onHover={onHover}
          width={chartWidth}
          height={chartHeight}
        />
      </div>
    </div>
  );
}

// ---------------- inner chart ----------------

function OverlayChart({
  title,
  yLabel,
  series,
  accessor,
  xMode,
  robustScale = false,
  selectedApi10s,
  cohortApi10s,
  hoveredApi10 = null,
  onHover,
  width = 430,
  height = 240,
}: {
  title: string;
  yLabel: string;
  series: WellSeries[];
  accessor: (s: WellSeries) => Array<number | null>;
  // X basis: "months" plots each point at its array index (months since
  // first prod, the classic aligned overlay); "date" plots at the
  // point's own absolute calendar month (WellSeries.monthIdx).
  xMode: XAxisMode;
  // Percentile-fit the Y axis instead of max-fit. Used by the GOR/WOR
  // ratio modes, where one dirty month (near-zero oil against normal
  // gas/water) can put a 10^5–10^6 point on a linear axis and flatten
  // every real curve. The data itself is untouched — spikes clip off
  // the top of the plot and get counted in an annotation.
  robustScale?: boolean;
  selectedApi10s?: Set<string>;
  cohortApi10s?: Set<string>;
  hoveredApi10?: string | null;
  onHover?: (api10: string | null) => void;
  width?: number;
  height?: number;
}) {
  // Unique per chart instance — two charts render side by side, and
  // duplicate clipPath ids would silently clip both against one rect.
  const clipId = useId();
  const plot = {
    x: PAD.left,
    y: PAD.top,
    w: width - PAD.left - PAD.right,
    h: height - PAD.top - PAD.bottom,
  };

  // X domain: months mode spans 0..max series length (months since
  // first prod); date mode spans the earliest..latest absolute prod
  // month across the cohort. Auto-fit Y to the visible max (with a
  // small headroom).
  let xMin = 0;
  let xMax = 0;
  let dataMax = 0;
  const pooled: number[] = [];
  let first = true;
  for (const s of series) {
    const arr = accessor(s);
    if (xMode === "months") {
      if (arr.length > xMax) xMax = arr.length;
    } else if (s.monthIdx.length > 0) {
      const lo = s.monthIdx[0]!;
      const hi = s.monthIdx[s.monthIdx.length - 1]!;
      if (first || lo < xMin) xMin = lo;
      if (first || hi > xMax) xMax = hi;
      first = false;
    }
    for (const v of arr) {
      if (v == null) continue;
      if (v > dataMax) dataMax = v;
      if (robustScale) pooled.push(v);
    }
  }
  if (xMax <= xMin) xMax = xMin + 1;

  // Smart scaling: fit the axis to the P98 of all plotted points when
  // the true max sits well above it (dirty data), otherwise keep the
  // exact max-fit so clean cohorts never clip. Small samples always
  // max-fit — a percentile of a handful of points is noise.
  let yFit = dataMax;
  if (robustScale && pooled.length >= 10) {
    const p98 = quantile(pooled, 0.98);
    if (p98 > 0 && dataMax > p98 * 1.5) yFit = p98;
  }
  const yMax = yFit * 1.05 || 1;
  const clippedCount = robustScale
    ? pooled.reduce((n, v) => (v > yMax ? n + 1 : n), 0)
    : 0;

  const xScale = (v: number) =>
    plot.x + ((v - xMin) / (xMax - xMin)) * plot.w;
  const yScale = (v: number) =>
    plot.y + plot.h - (v / yMax) * plot.h;

  const xTicks =
    xMode === "months" ? niceTicks(0, xMax, 6) : dateTicks(xMin, xMax, 6);
  const yTicks = niceTicks(0, yMax, 5);
  const xTickStep = xTicks.length >= 2 ? xTicks[1]! - xTicks[0]! : 12;

  return (
    <svg
      width={width}
      height={height}
      className="inspect-overlay-svg"
      role="img"
      aria-label={title}
    >
      <text
        x={width / 2}
        y={14}
        textAnchor="middle"
        fontSize="13"
        fontWeight={600}
        fill="#0f172a"
      >
        {title}
      </text>

      <rect
        x={plot.x}
        y={plot.y}
        width={plot.w}
        height={plot.h}
        fill="#fff"
        stroke="#e5e7eb"
      />

      {/* Y grid */}
      {yTicks.map((t) => (
        <g key={`y${t}`}>
          <line
            x1={plot.x}
            x2={plot.x + plot.w}
            y1={yScale(t)}
            y2={yScale(t)}
            stroke="#f3f4f6"
          />
          <text
            x={plot.x - 6}
            y={yScale(t)}
            textAnchor="end"
            dominantBaseline="middle"
            fontSize="11"
            fill="#6b7280"
          >
            {formatTick(t)}
          </text>
        </g>
      ))}
      {/* X grid */}
      {xTicks.map((t) => (
        <g key={`x${t}`}>
          <line
            x1={xScale(t)}
            x2={xScale(t)}
            y1={plot.y}
            y2={plot.y + plot.h}
            stroke="#f3f4f6"
          />
          <text
            x={xScale(t)}
            y={plot.y + plot.h + 12}
            textAnchor="middle"
            fontSize="11"
            fill="#6b7280"
          >
            {xMode === "months"
              ? Math.round(t)
              : formatMonthTick(t, xTickStep)}
          </text>
        </g>
      ))}

      {/* One polyline per well. Break on nulls (NaN segment trick:
          start a new <polyline> for each contiguous run of non-null
          points instead). Keeps gaps visible.

          Sort so the hovered well's polyline draws last (on top of its
          peers); selection toggles per-well opacity to match the
          gun-barrel's dimmed-circle convention.

          Clipped to the plot rect so off-scale points (robustScale)
          run off the top edge instead of drawing over the title. */}
      <defs>
        <clipPath id={clipId}>
          <rect x={plot.x} y={plot.y} width={plot.w} height={plot.h} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
      {[...series]
        .sort(
          (a, b) =>
            (a.api10 === hoveredApi10 ? 1 : 0) -
            (b.api10 === hoveredApi10 ? 1 : 0),
        )
        .map((s) => {
          const color = colorForFormation(s.formation);
          const arr = accessor(s);
          const segments: Array<Array<[number, number]>> = [];
          let current: Array<[number, number]> = [];
          for (let i = 0; i < arr.length; i++) {
            const v = arr[i];
            const x = xMode === "months" ? i : s.monthIdx[i];
            if (v == null || x == null) {
              if (current.length > 0) segments.push(current);
              current = [];
            } else {
              current.push([x, v]);
            }
          }
          if (current.length > 0) segments.push(current);

          const isSelected = selectedApi10s?.has(s.api10) ?? true;
          const isHovered = hoveredApi10 === s.api10;
          const anyHover = hoveredApi10 != null;
          // Deselected wells stay ghosted regardless of hover so the
          // user can still see them but they don't compete with the
          // signal. Selected wells dim slightly when *another* well
          // is hovered so the highlight reads.
          const strokeOpacity = !isSelected
            ? 0.15
            : anyHover && !isHovered
              ? 0.25
              : 0.85;
          const strokeWidth = isSelected && isHovered ? 2.5 : 1.25;
          // Cohort members get a wider sky-blue halo stroke under their
          // line — the line-overlay analog of the gun-barrel circle halo
          // and the map's cohort sticks. Dimmed for deselected wells so
          // a ghosted line isn't wrapped in a bright halo.
          const inCohort = cohortApi10s?.has(s.api10) ?? false;
          const points = segments.map((seg) =>
            seg.map(([x, y]) => `${xScale(x)},${yScale(y)}`).join(" "),
          );
          return (
            <g
              key={s.api10}
              style={{ cursor: "pointer" }}
              onMouseEnter={() => onHover?.(s.api10)}
              onMouseLeave={() => onHover?.(null)}
            >
              {inCohort &&
                points.map((pts, idx) => (
                  <polyline
                    key={`halo-${s.api10}-${idx}`}
                    fill="none"
                    stroke={COHORT_HALO_COLOR}
                    strokeWidth={strokeWidth + 4}
                    strokeOpacity={isSelected ? 0.5 : 0.15}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    points={pts}
                  />
                ))}
              {points.map((pts, idx) => (
                <polyline
                  key={`${s.api10}-${idx}`}
                  fill="none"
                  stroke={color}
                  strokeWidth={strokeWidth}
                  strokeOpacity={strokeOpacity}
                  points={pts}
                />
              ))}
            </g>
          );
        })}
      </g>

      {/* Off-scale flag — the honest half of robust scaling: the axis
          ignores the top outliers, but the chart says how many points
          left the frame so a spiky well reads as dirty data, not as
          missing data. */}
      {clippedCount > 0 && (
        <text
          x={plot.x + plot.w - 4}
          y={plot.y + 10}
          textAnchor="end"
          fontSize="11"
          fill="#b91c1c"
        >
          ▲ {clippedCount} pt{clippedCount === 1 ? "" : "s"} off-scale
        </text>
      )}

      {/* Axis labels */}
      <text
        x={plot.x + plot.w / 2}
        y={height - 4}
        textAnchor="middle"
        fontSize="12"
        fill="#374151"
      >
        {xMode === "months" ? "months since first prod" : "prod month"}
      </text>
      <text
        x={14}
        y={plot.y + plot.h / 2}
        transform={`rotate(-90 14 ${plot.y + plot.h / 2})`}
        textAnchor="middle"
        fontSize="12"
        fill="#374151"
      >
        {yLabel}
      </text>
    </svg>
  );
}

// ---------------- formatting ----------------

// Linear-interpolated quantile (numpy default). Sorts a copy — pooled
// arrays here are a few thousand points at most.
function quantile(values: number[], q: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  const frac = pos - lo;
  return sorted[lo]! * (1 - frac) + sorted[hi]! * frac;
}

// Calendar ticks for date mode, in absolute month-index space. Steps
// are month-friendly (quarters, half-years, years, ...) so ticks land
// on natural boundaries — a 12-multiple index is a January, so
// year-step ticks label as bare years.
const DATE_TICK_STEPS = [1, 2, 3, 6, 12, 24, 60, 120] as const;

function dateTicks(minIdx: number, maxIdx: number, count: number): number[] {
  const span = Math.max(maxIdx - minIdx, 1);
  const step = DATE_TICK_STEPS.find((s) => span / s <= count) ?? 120;
  const out: number[] = [];
  for (let t = Math.ceil(minIdx / step) * step; t <= maxIdx; t += step) {
    out.push(t);
  }
  return out.length > 0 ? out : [minIdx];
}

function formatMonthTick(idx: number, stepMonths: number): string {
  const y = Math.floor(idx / 12);
  const m = (idx % 12) + 1;
  // Year-or-coarser steps tick on Januaries — the year alone reads
  // cleaner than a redundant "01/".
  if (stepMonths >= 12) return String(y);
  return `${String(m).padStart(2, "0")}/${String(y).slice(2)}`;
}

function formatTick(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  if (abs >= 100) return v.toFixed(0);
  if (abs >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

function niceTicks(min: number, max: number, count: number): number[] {
  if (min === max) return [min];
  const step = niceStep((max - min) / count);
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + 1e-9; v += step) out.push(round(v));
  return out;
}

function niceStep(rough: number): number {
  if (rough <= 0) return 1;
  const exp = Math.floor(Math.log10(rough));
  const frac = rough / Math.pow(10, exp);
  let mult: number;
  if (frac < 1.5) mult = 1;
  else if (frac < 3) mult = 2;
  else if (frac < 7) mult = 5;
  else mult = 10;
  return mult * Math.pow(10, exp);
}

function round(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}
