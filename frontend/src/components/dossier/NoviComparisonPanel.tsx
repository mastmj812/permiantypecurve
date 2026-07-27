// TC-vs-Novi comparison figure (2026-07-27 amendment): a 6-panel grid
// (oil/gas/water x rate/cum) putting the zone's type curve — P90–P10
// band + P50 + mean, per-1,000-ft, peak-fit — against the MEDIAN Novi
// Intelligence ML forecast of the representative stick set (IP-aligned,
// per-1,000-ft, from /api/deals/{id}/novi-comparison). Rate panels are
// log-log, cum panels linear with end-point labels, mirroring the
// engineering reference figure. Captured to PNG for the dossier's
// comparison slide; the workbook's novi_comparison sheet carries the
// same Novi series as period volumes.
//
// Risking: the TC side renders RISKED (each curve by its own
// multipliers — same rule as the slide charts); the Novi median is a
// vendor benchmark and is never risked.

import { useMemo } from "react";

import type { NoviComparisonZone } from "../../api/deals";
import type { StreamSeries, TypeCurveRow } from "../../api/typeCurves";

import { DAYS_PER_MONTH, buildRampArpsRate } from "../../forecasts/arps";
import { normalizeMultipliers, riskStreamSeries } from "../../type_curves/risking";

const N_MONTHS = 600;

const TC_BAND = "#c7d9e5";
const TC_P50 = "#2b6a8f";
const TC_MEAN = "#4d88ab";
const NOVI_RED = "#c0392b";
const GRID = "#e5e7eb";
const AXIS = "#6b7280";

type StreamKey = "oil" | "gas" | "water";
const STREAMS: Array<{ key: StreamKey; label: string; rateUnit: string; cumUnit: string }> = [
  { key: "oil", label: "Oil", rateUnit: "BOPD/1000ft", cumUnit: "BO/1000ft" },
  { key: "gas", label: "Gas", rateUnit: "MCFD/1000ft", cumUnit: "MCF/1000ft" },
  { key: "water", label: "Water", rateUnit: "BWPD/1000ft", cumUnit: "BW/1000ft" },
];

interface Props {
  curve: TypeCurveRow;
  zone: NoviComparisonZone;
  width?: number;
  height?: number;
}

interface StreamData {
  label: string;
  rateUnit: string;
  cumUnit: string;
  hi: number[]; // SPE P10 (high) rates, per-1000ft/day
  lo: number[]; // SPE P90 (low)
  p50: number[];
  mean: number[];
  novi: number[]; // median Novi ML rates
  cumP50: number[];
  cumHi: number[];
  cumLo: number[];
  cumMean: number[];
  cumNovi: number[];
}

function cumFrom(rates: number[], dt: number): number[] {
  // Trapezoid, anchored at t=0 — the shared integration convention.
  const out: number[] = [];
  let cum = 0;
  for (let i = 0; i < rates.length; i++) {
    const raw = rates[i];
    const a = raw !== undefined && Number.isFinite(raw) ? raw : 0;
    const nxt = rates[i + 1];
    const b = nxt !== undefined && Number.isFinite(nxt) ? nxt : a;
    cum += ((a + b) / 2) * dt;
    out.push(cum);
  }
  return out;
}

// Evaluate one percentile's persisted Arps fit over the full horizon;
// fall back to the empirical aggregate array when the fit is missing
// (older saved curves) — the band then just ends where the data does.
function fullRates(s: StreamSeries, key: "p10" | "p50" | "p90" | "mean"): number[] {
  const fit = s.fitted_per_percentile?.[key];
  if (fit) return buildRampArpsRate(fit, N_MONTHS);
  const arr = s[key] ?? [];
  return arr.map((v) => (v == null || !Number.isFinite(v) ? 0 : v));
}

function buildStreamData(curve: TypeCurveRow, zone: NoviComparisonZone): StreamData[] {
  const muls = normalizeMultipliers(curve.risk_multipliers);
  const streams = (curve.series as { streams?: Record<string, StreamSeries> }).streams ?? {};
  const noviByStream: Record<StreamKey, number[]> = {
    oil: zone.oil_rate,
    gas: zone.gas_rate,
    water: zone.water_rate,
  };
  return STREAMS.map(({ key, label, rateUnit, cumUnit }) => {
    const raw = streams[key];
    const s = raw ? riskStreamSeries(raw, muls[key]) : null;
    const hi = s ? fullRates(s, "p10") : [];
    const lo = s ? fullRates(s, "p90") : [];
    const p50 = s ? fullRates(s, "p50") : [];
    const mean = s ? fullRates(s, "mean") : [];
    const novi = noviByStream[key] ?? [];
    return {
      label,
      rateUnit,
      cumUnit,
      hi,
      lo,
      p50,
      mean,
      novi,
      cumHi: cumFrom(hi, DAYS_PER_MONTH),
      cumLo: cumFrom(lo, DAYS_PER_MONTH),
      cumP50: cumFrom(p50, DAYS_PER_MONTH),
      cumMean: cumFrom(mean, DAYS_PER_MONTH),
      // Novi cums integrate on the Novi-native 30-day grid so the
      // chart's asymptote equals the workbook sheet's column sum.
      cumNovi: cumFrom(novi, zone.step_days),
    };
  });
}

const fmt = (v: number): string =>
  Math.round(v).toLocaleString("en-US");

// ---------------------------------------------------------------------------

interface Frame {
  x: number;
  y: number;
  w: number;
  h: number;
}

function logPath(
  xs: number[],
  frame: Frame,
  yMin: number,
  yMax: number,
  xMax: number,
): string {
  // log-log: x = month (1-based), y = rate; skip non-positive values.
  const lx = (m: number) =>
    frame.x + (Math.log10(m) / Math.log10(xMax)) * frame.w;
  const ly = (v: number) =>
    frame.y + frame.h -
    ((Math.log10(v) - Math.log10(yMin)) /
      (Math.log10(yMax) - Math.log10(yMin))) * frame.h;
  let d = "";
  for (let i = 0; i < xs.length; i++) {
    const v = xs[i];
    if (v === undefined || !Number.isFinite(v) || v <= yMin) {
      continue;
    }
    const cmd = d === "" ? "M" : "L";
    d += `${cmd}${lx(i + 1).toFixed(1)},${ly(Math.min(v, yMax)).toFixed(1)}`;
  }
  return d;
}

function linPath(
  xs: number[],
  frame: Frame,
  yMax: number,
  xMax: number,
): string {
  const lx = (m: number) => frame.x + (m / xMax) * frame.w;
  const ly = (v: number) => frame.y + frame.h - (v / yMax) * frame.h;
  let d = `M${lx(0).toFixed(1)},${ly(0).toFixed(1)}`;
  for (let i = 0; i < xs.length; i++) {
    const raw = xs[i];
    const v = raw !== undefined && Number.isFinite(raw) ? raw : 0;
    d += `L${lx(i + 1).toFixed(1)},${ly(Math.min(v, yMax)).toFixed(1)}`;
  }
  return d;
}

function bandPath(
  hi: number[],
  lo: number[],
  frame: Frame,
  yMin: number,
  yMax: number,
  xMax: number,
): string {
  const lx = (m: number) =>
    frame.x + (Math.log10(m) / Math.log10(xMax)) * frame.w;
  const ly = (v: number) =>
    frame.y + frame.h -
    ((Math.log10(Math.max(v, yMin)) - Math.log10(yMin)) /
      (Math.log10(yMax) - Math.log10(yMin))) * frame.h;
  const up: string[] = [];
  const down: string[] = [];
  const n = Math.min(hi.length, lo.length);
  for (let i = 0; i < n; i++) {
    const h = hi[i];
    const l = lo[i] ?? 0;
    if (h === undefined || !Number.isFinite(h) || h <= yMin) continue;
    up.push(`${lx(i + 1).toFixed(1)},${ly(Math.min(h, yMax)).toFixed(1)}`);
    down.push(`${lx(i + 1).toFixed(1)},${ly(Math.min(Math.max(l, yMin), yMax)).toFixed(1)}`);
  }
  if (up.length === 0) return "";
  return `M${up.join("L")}L${down.reverse().join("L")}Z`;
}

export function NoviComparisonPanel({
  curve,
  zone,
  // COMPARISON_PANEL_PX on the backend (12.05" x 5.5" at 96 px/in) so
  // the captured PNG aspect equals the slide picture box.
  width = 1157,
  height = 528,
}: Props) {
  const data = useMemo(() => buildStreamData(curve, zone), [curve, zone]);

  const cols = 2;
  const rows = 3;
  const cellW = width / cols;
  const cellH = height / rows;
  const mL = 58;
  const mR = 74;
  const mT = 20;
  const mB = 24;

  const panels: Array<JSX.Element> = [];
  data.forEach((sd, row) => {
    // ---- rate panel (log-log) ----
    {
      const frame: Frame = {
        x: 0 * cellW + mL,
        y: row * cellH + mT,
        w: cellW - mL - 18,
        h: cellH - mT - mB,
      };
      const allVals = [...sd.hi, ...sd.novi].filter((v) => Number.isFinite(v) && v > 0);
      const rawMax = allVals.length ? Math.max(...allVals) : 1;
      const yMax = 10 ** Math.ceil(Math.log10(rawMax));
      const yMin = Math.max(10 ** (Math.ceil(Math.log10(rawMax)) - 4), 1e-3);
      const xMax = N_MONTHS;
      const decades: number[] = [];
      for (let d = Math.log10(yMin); d <= Math.log10(yMax) + 1e-9; d++) {
        decades.push(10 ** d);
      }
      const xTicks = [1, 10, 100];
      panels.push(
        <g key={`rate-${sd.label}`}>
          <text x={frame.x + frame.w / 2} y={frame.y - 7} textAnchor="middle" fontSize={12} fontWeight={600} fill="#111827">
            {sd.label} — rate vs time
          </text>
          <rect x={frame.x} y={frame.y} width={frame.w} height={frame.h} fill="none" stroke={AXIS} strokeWidth={0.75} />
          {decades.map((v) => {
            const y = frame.y + frame.h -
              ((Math.log10(v) - Math.log10(yMin)) / (Math.log10(yMax) - Math.log10(yMin))) * frame.h;
            return (
              <g key={v}>
                <line x1={frame.x} y1={y} x2={frame.x + frame.w} y2={y} stroke={GRID} strokeWidth={0.5} />
                <text x={frame.x - 4} y={y + 3} textAnchor="end" fontSize={9} fill={AXIS}>
                  {v >= 1 ? fmt(v) : v}
                </text>
              </g>
            );
          })}
          {xTicks.map((m) => {
            const x = frame.x + (Math.log10(m) / Math.log10(xMax)) * frame.w;
            return (
              <g key={m}>
                <line x1={x} y1={frame.y} x2={x} y2={frame.y + frame.h} stroke={GRID} strokeWidth={0.5} />
                <text x={x} y={frame.y + frame.h + 12} textAnchor="middle" fontSize={9} fill={AXIS}>
                  {m}
                </text>
              </g>
            );
          })}
          <text
            x={frame.x - 44}
            y={frame.y + frame.h / 2}
            textAnchor="middle"
            fontSize={10}
            fill={AXIS}
            transform={`rotate(-90 ${frame.x - 44} ${frame.y + frame.h / 2})`}
          >
            {sd.rateUnit}
          </text>
          <path d={bandPath(sd.hi, sd.lo, frame, yMin, yMax, xMax)} fill={TC_BAND} fillOpacity={0.55} stroke="none" />
          <path d={logPath(sd.mean, frame, yMin, yMax, xMax)} fill="none" stroke={TC_MEAN} strokeWidth={1.2} strokeDasharray="5 3" />
          <path d={logPath(sd.p50, frame, yMin, yMax, xMax)} fill="none" stroke={TC_P50} strokeWidth={2} />
          <path d={logPath(sd.novi, frame, yMin, yMax, xMax)} fill="none" stroke={NOVI_RED} strokeWidth={1.8} />
          {row === 0 && (
            <g fontSize={9.5} fill="#374151">
              <rect x={frame.x + 8} y={frame.y + 6} width={128} height={54} fill="#ffffff" fillOpacity={0.85} stroke={GRID} />
              <rect x={frame.x + 14} y={frame.y + 12} width={16} height={8} fill={TC_BAND} />
              <text x={frame.x + 35} y={frame.y + 19}>TC P90–P10</text>
              <line x1={frame.x + 14} y1={frame.y + 29} x2={frame.x + 30} y2={frame.y + 29} stroke={TC_P50} strokeWidth={2} />
              <text x={frame.x + 35} y={frame.y + 32}>TC P50</text>
              <line x1={frame.x + 14} y1={frame.y + 41} x2={frame.x + 30} y2={frame.y + 41} stroke={TC_MEAN} strokeWidth={1.2} strokeDasharray="5 3" />
              <text x={frame.x + 35} y={frame.y + 44}>TC mean</text>
              <line x1={frame.x + 14} y1={frame.y + 53} x2={frame.x + 30} y2={frame.y + 53} stroke={NOVI_RED} strokeWidth={1.8} />
              <text x={frame.x + 35} y={frame.y + 56}>Novi ML median</text>
            </g>
          )}
          <text x={frame.x + frame.w / 2} y={frame.y + frame.h + 12} textAnchor="middle" fontSize={9} fill={AXIS} dx={40}>
            months
          </text>
        </g>,
      );
    }
    // ---- cum panel (linear) ----
    {
      const frame: Frame = {
        x: 1 * cellW + mL,
        y: row * cellH + mT,
        w: cellW - mL - mR,
        h: cellH - mT - mB,
      };
      const endNovi = sd.cumNovi.at(-1) ?? 0;
      const endP50 = sd.cumP50.at(-1) ?? 0;
      const endHi = sd.cumHi.at(-1) ?? 0;
      const yMax = Math.max(endNovi, endHi, 1) * 1.05;
      const xMax = N_MONTHS;
      const yTicks = [0.25, 0.5, 0.75, 1.0].map((f) => f * yMax);
      const xTicks = [0, 100, 200, 300, 400, 500, 600];
      const ly = (v: number) => frame.y + frame.h - (v / yMax) * frame.h;
      panels.push(
        <g key={`cum-${sd.label}`}>
          <text x={frame.x + frame.w / 2} y={frame.y - 7} textAnchor="middle" fontSize={12} fontWeight={600} fill="#111827">
            {sd.label} — cum vs time
          </text>
          <rect x={frame.x} y={frame.y} width={frame.w} height={frame.h} fill="none" stroke={AXIS} strokeWidth={0.75} />
          {yTicks.map((v) => (
            <g key={v}>
              <line x1={frame.x} y1={ly(v)} x2={frame.x + frame.w} y2={ly(v)} stroke={GRID} strokeWidth={0.5} />
              <text x={frame.x - 4} y={ly(v) + 3} textAnchor="end" fontSize={9} fill={AXIS}>
                {fmt(v)}
              </text>
            </g>
          ))}
          {xTicks.map((m) => {
            const x = frame.x + (m / xMax) * frame.w;
            return (
              <text key={m} x={x} y={frame.y + frame.h + 12} textAnchor="middle" fontSize={9} fill={AXIS}>
                {m}
              </text>
            );
          })}
          <text
            x={frame.x - 46}
            y={frame.y + frame.h / 2}
            textAnchor="middle"
            fontSize={10}
            fill={AXIS}
            transform={`rotate(-90 ${frame.x - 46} ${frame.y + frame.h / 2})`}
          >
            {`cum (${sd.cumUnit})`}
          </text>
          <path d={linPath(sd.cumMean, frame, yMax, xMax)} fill="none" stroke={TC_MEAN} strokeWidth={1.2} strokeDasharray="5 3" />
          <path d={linPath(sd.cumP50, frame, yMax, xMax)} fill="none" stroke={TC_P50} strokeWidth={2} />
          <path d={linPath(sd.cumNovi, frame, yMax, xMax)} fill="none" stroke={NOVI_RED} strokeWidth={1.8} />
          <text x={frame.x + frame.w + 3} y={ly(endNovi) + 3} fontSize={9} fill={NOVI_RED}>
            {`Novi ${fmt(endNovi)}`}
          </text>
          <text x={frame.x + frame.w + 3} y={ly(endP50) + (Math.abs(ly(endP50) - ly(endNovi)) < 10 ? 13 : 3)} fontSize={9} fill={TC_P50}>
            {`TC P50 ${fmt(endP50)}`}
          </text>
        </g>,
      );
    }
  });

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ background: "#ffffff", display: "block" }}
    >
      {panels}
    </svg>
  );
}
