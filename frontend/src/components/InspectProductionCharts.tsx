// InspectProductionCharts — paired rate-vs-time + cum-vs-time overlay
// charts for the staged wells inside the inspect modal. All wells on
// one set of axes, first-prod-aligned (x = months since first prod),
// colored to match the gun-barrel circles (by formation).
//
// Toggle: stream (oil/gas/water) and normalize-per-10kft. The user
// asked for per-10,000-ft lateral as the default — the documented
// trap is that raw rates make longer laterals look like better wells
// when they're really just longer.

import { useEffect, useMemo, useState } from "react";

import type { WellDetailLite } from "../api/wells";
import {
  fetchWellCurves,
  type Stream,
  type WellCurvesResponse,
} from "../api/forecasts";
import { colorForFormation } from "../map/formations";

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
  // Lifted hover state — when set, the matching polyline bolds up
  // (and the gun-barrel circle bolds too via the same prop on the
  // sibling component).
  hoveredApi10?: string | null;
  onHover?: (api10: string | null) => void;
}

const PAD = { top: 24, right: 16, bottom: 36, left: 56 };
const STREAMS: Stream[] = ["oil", "gas", "water"];

interface WellSeries {
  api10: string;
  formation: string | null;
  // Aligned to "months since first prod" — index = X position (months).
  // null entries preserve the gaps (downtime) that history_rate may
  // have, so the polyline breaks cleanly across them.
  rate: Array<number | null>;
  cum: Array<number | null>;
}

export function InspectProductionCharts({
  api10s,
  wellsByApi10,
  selectedApi10s,
  hoveredApi10 = null,
  onHover,
}: InspectProductionChartsProps) {
  const [stream, setStream] = useState<Stream>("oil");
  const [normalize, setNormalize] = useState<boolean>(true);
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

  // Build per-well aligned series for the active stream + normalize
  // mode. Memoized so toggling normalize doesn't re-fetch.
  const series = useMemo<WellSeries[]>(() => {
    if (!curves) return [];
    const out: WellSeries[] = [];
    for (const api10 of api10s) {
      const resp = curves.get(api10);
      if (!resp) continue;
      const sc = resp.streams.find((s) => s.stream === stream);
      if (!sc) continue;
      const meta = wellsByApi10.get(api10) ?? null;
      const lateralFt = meta?.lateral_ft ?? null;
      // Normalize per 10kft when requested AND lateral is known.
      // Unknown-lateral wells fall back to raw so a missing length
      // doesn't yank them off the chart (an open question the user
      // didn't litigate; raw is the conservative default).
      const norm =
        normalize && lateralFt && lateralFt > 0 ? 10_000 / lateralFt : 1;

      const rate: Array<number | null> = sc.history_rate.map((v) =>
        v == null ? null : v * norm,
      );
      const cum: Array<number | null> = sc.history_cum.map((v) =>
        v == null ? null : v * norm,
      );
      out.push({
        api10,
        formation: meta?.formation ?? null,
        rate,
        cum,
      });
    }
    return out;
  }, [curves, api10s, stream, normalize, wellsByApi10]);

  // The "hide entirely" rule from the plan: fewer than 2 wells with
  // any non-null production → don't bother showing the panel. Caller
  // can still render whatever headers/footers it wants around us.
  const wellsWithData = series.filter((s) =>
    s.rate.some((v) => v != null && v > 0),
  );
  const showCharts = wellsWithData.length >= 2;

  if (loading) {
    return <div className="inspect-charts-empty">Loading production…</div>;
  }
  if (error) {
    return <div className="inspect-charts-empty">Failed to load: {error}</div>;
  }
  if (!showCharts) {
    return (
      <div className="inspect-charts-empty">
        Production overlay hidden — needs ≥2 wells with history.
      </div>
    );
  }

  return (
    <div className="inspect-charts">
      <div className="inspect-charts-controls">
        <div className="inspect-charts-control-group">
          <span className="inspect-charts-control-label">Stream</span>
          {STREAMS.map((s) => (
            <button
              key={s}
              type="button"
              className={`inspect-charts-pill ${
                stream === s ? "is-active" : ""
              }`}
              onClick={() => setStream(s)}
            >
              {s}
            </button>
          ))}
        </div>
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
      </div>

      <div className="inspect-charts-row">
        <OverlayChart
          title={`${stream.toUpperCase()} rate vs. months from first prod`}
          yLabel={normalize ? "rate / 10kft" : "rate"}
          series={wellsWithData}
          accessor={(s) => s.rate}
          selectedApi10s={selectedApi10s}
          hoveredApi10={hoveredApi10}
          onHover={onHover}
        />
        <OverlayChart
          title={`${stream.toUpperCase()} cum vs. months from first prod`}
          yLabel={normalize ? "cum / 10kft" : "cum"}
          series={wellsWithData}
          accessor={(s) => s.cum}
          selectedApi10s={selectedApi10s}
          hoveredApi10={hoveredApi10}
          onHover={onHover}
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
  selectedApi10s,
  hoveredApi10 = null,
  onHover,
  width = 430,
  height = 240,
}: {
  title: string;
  yLabel: string;
  series: WellSeries[];
  accessor: (s: WellSeries) => Array<number | null>;
  selectedApi10s?: Set<string>;
  hoveredApi10?: string | null;
  onHover?: (api10: string | null) => void;
  width?: number;
  height?: number;
}) {
  const plot = {
    x: PAD.left,
    y: PAD.top,
    w: width - PAD.left - PAD.right,
    h: height - PAD.top - PAD.bottom,
  };

  // X = max series length across the cohort (months since first
  // prod). Auto-fit Y to the visible max (with a small headroom).
  let xMax = 0;
  let yMax = 0;
  for (const s of series) {
    const arr = accessor(s);
    if (arr.length > xMax) xMax = arr.length;
    for (const v of arr) {
      if (v != null && v > yMax) yMax = v;
    }
  }
  if (xMax === 0) xMax = 1;
  yMax = yMax * 1.05 || 1;

  const xScale = (v: number) => plot.x + (v / xMax) * plot.w;
  const yScale = (v: number) =>
    plot.y + plot.h - (v / yMax) * plot.h;

  const xTicks = niceTicks(0, xMax, 6);
  const yTicks = niceTicks(0, yMax, 5);

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
        fontSize="11"
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
            fontSize="9"
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
            fontSize="9"
            fill="#6b7280"
          >
            {Math.round(t)}
          </text>
        </g>
      ))}

      {/* One polyline per well. Break on nulls (NaN segment trick:
          start a new <polyline> for each contiguous run of non-null
          points instead). Keeps gaps visible.

          Sort so the hovered well's polyline draws last (on top of its
          peers); selection toggles per-well opacity to match the
          gun-barrel's dimmed-circle convention. */}
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
            if (v == null) {
              if (current.length > 0) segments.push(current);
              current = [];
            } else {
              current.push([i, v]);
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
          return (
            <g
              key={s.api10}
              style={{ cursor: "pointer" }}
              onMouseEnter={() => onHover?.(s.api10)}
              onMouseLeave={() => onHover?.(null)}
            >
              {segments.map((seg, idx) => (
                <polyline
                  key={`${s.api10}-${idx}`}
                  fill="none"
                  stroke={color}
                  strokeWidth={strokeWidth}
                  strokeOpacity={strokeOpacity}
                  points={seg
                    .map(([x, y]) => `${xScale(x)},${yScale(y)}`)
                    .join(" ")}
                />
              ))}
            </g>
          );
        })}

      {/* Axis labels */}
      <text
        x={plot.x + plot.w / 2}
        y={height - 4}
        textAnchor="middle"
        fontSize="10"
        fill="#374151"
      >
        months since first prod
      </text>
      <text
        x={14}
        y={plot.y + plot.h / 2}
        transform={`rotate(-90 14 ${plot.y + plot.h / 2})`}
        textAnchor="middle"
        fontSize="10"
        fill="#374151"
      >
        {yLabel}
      </text>
    </svg>
  );
}

// ---------------- formatting ----------------

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
