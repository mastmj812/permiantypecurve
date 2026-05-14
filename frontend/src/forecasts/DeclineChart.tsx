// Hand-rolled SVG decline chart. Supports linear and log Y axes (log is
// the standard "semi-log decline" view used in petroleum engineering).
//
// Two series: history (actuals, drawn as points + thin line) and forecast
// (smooth line). Axis ticks are computed from the combined data range.
//
// Why not Plotly: ~3 MB bundle, fiddly React integration, and we only
// need two trace types + log toggle. SVG is 150 lines and renders the
// same data with no dependency.

import { useMemo } from "react";

export type AxisType = "linear" | "log";

export interface SeriesPoint {
  t: number; // months since peak
  y: number;
}

interface Props {
  history: SeriesPoint[];
  forecast: SeriesPoint[];
  yAxisType: AxisType;
  yLabel: string;
  xLabel?: string;
  width?: number;
  height?: number;
}

const PAD = { top: 16, right: 16, bottom: 36, left: 56 };

export function DeclineChart({
  history,
  forecast,
  yAxisType,
  yLabel,
  xLabel = "Months since peak",
  width = 520,
  height = 280,
}: Props) {
  const { xScale, yScale, xTicks, yTicks, plotArea } = useMemo(
    () => computeAxes({ history, forecast, yAxisType, width, height }),
    [history, forecast, yAxisType, width, height],
  );

  const historyPath = pointsToPath(history, xScale, yScale, yAxisType);
  const forecastPath = pointsToPath(forecast, xScale, yScale, yAxisType);

  return (
    <svg width={width} height={height} className="decline-chart">
      {/* Plot area background */}
      <rect
        x={plotArea.x}
        y={plotArea.y}
        width={plotArea.w}
        height={plotArea.h}
        fill="#fff"
        stroke="#e5e7eb"
      />

      {/* Y grid + ticks */}
      {yTicks.map((t) => (
        <g key={`y${t.label}`}>
          <line
            x1={plotArea.x}
            x2={plotArea.x + plotArea.w}
            y1={yScale(t.value)}
            y2={yScale(t.value)}
            stroke="#f3f4f6"
          />
          <text
            x={plotArea.x - 6}
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

      {/* X grid + ticks */}
      {xTicks.map((t) => (
        <g key={`x${t}`}>
          <line
            x1={xScale(t)}
            x2={xScale(t)}
            y1={plotArea.y}
            y2={plotArea.y + plotArea.h}
            stroke="#f3f4f6"
          />
          <text
            x={xScale(t)}
            y={plotArea.y + plotArea.h + 14}
            textAnchor="middle"
            fontSize="10"
            fill="#6b7280"
          >
            {t}
          </text>
        </g>
      ))}

      {/* Forecast (drawn first so history sits on top) */}
      {forecast.length > 1 && (
        <path
          d={forecastPath}
          fill="none"
          stroke="#dc2626"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        />
      )}

      {/* History points + connecting line */}
      {history.length > 1 && (
        <path d={historyPath} fill="none" stroke="#1f2937" strokeWidth={1.25} />
      )}
      {history.map((p) =>
        validForAxis(p.y, yAxisType) ? (
          <circle
            key={`h${p.t}`}
            cx={xScale(p.t)}
            cy={yScale(p.y)}
            r={2}
            fill="#1f2937"
          />
        ) : null,
      )}

      {/* Axis labels */}
      <text
        x={plotArea.x + plotArea.w / 2}
        y={height - 4}
        textAnchor="middle"
        fontSize="11"
        fill="#374151"
      >
        {xLabel}
      </text>
      <text
        x={12}
        y={plotArea.y + plotArea.h / 2}
        transform={`rotate(-90 12 ${plotArea.y + plotArea.h / 2})`}
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

interface AxesProps {
  history: SeriesPoint[];
  forecast: SeriesPoint[];
  yAxisType: AxisType;
  width: number;
  height: number;
}

interface AxesOut {
  xScale: (x: number) => number;
  yScale: (y: number) => number;
  xTicks: number[];
  yTicks: { value: number; label: string }[];
  plotArea: { x: number; y: number; w: number; h: number };
}

function computeAxes(p: AxesProps): AxesOut {
  const plotArea = {
    x: PAD.left,
    y: PAD.top,
    w: p.width - PAD.left - PAD.right,
    h: p.height - PAD.top - PAD.bottom,
  };

  const allPoints = [...p.history, ...p.forecast];
  if (allPoints.length === 0) {
    return {
      xScale: () => plotArea.x,
      yScale: () => plotArea.y + plotArea.h,
      xTicks: [],
      yTicks: [],
      plotArea,
    };
  }
  const xMin = 0;
  const xMax = Math.max(...allPoints.map((pp) => pp.t));
  const xRange = xMax - xMin || 1;
  const xScale = (x: number) => plotArea.x + ((x - xMin) / xRange) * plotArea.w;

  const yValues = allPoints
    .map((pp) => pp.y)
    .filter((v) => Number.isFinite(v) && (p.yAxisType !== "log" || v > 0));
  if (yValues.length === 0) {
    return {
      xScale,
      yScale: () => plotArea.y + plotArea.h,
      xTicks: niceTicks(xMin, xMax, 6),
      yTicks: [],
      plotArea,
    };
  }
  const yMin = p.yAxisType === "log" ? Math.max(1e-3, Math.min(...yValues)) : 0;
  const yMax = Math.max(...yValues);
  const yScale = (y: number) => {
    if (p.yAxisType === "log") {
      const ly = Math.log10(Math.max(y, yMin));
      const lyMin = Math.log10(yMin);
      const lyMax = Math.log10(yMax);
      const r = (ly - lyMin) / Math.max(lyMax - lyMin, 1e-9);
      return plotArea.y + plotArea.h * (1 - r);
    }
    const r = (y - yMin) / Math.max(yMax - yMin, 1e-9);
    return plotArea.y + plotArea.h * (1 - r);
  };

  return {
    xScale,
    yScale,
    xTicks: niceTicks(xMin, xMax, 6),
    yTicks:
      p.yAxisType === "log"
        ? logTicks(yMin, yMax)
        : niceTicks(yMin, yMax, 5).map((v) => ({ value: v, label: formatY(v) })),
    plotArea,
  };
}

function niceTicks(min: number, max: number, count: number): number[] {
  if (min === max) return [min];
  const step = niceStep((max - min) / count);
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + 1e-9; v += step) out.push(round(v));
  return out;
}

function logTicks(min: number, max: number): { value: number; label: string }[] {
  const lo = Math.floor(Math.log10(Math.max(min, 1e-3)));
  const hi = Math.ceil(Math.log10(Math.max(max, 1e-2)));
  const out: { value: number; label: string }[] = [];
  for (let p = lo; p <= hi; p++) {
    const v = Math.pow(10, p);
    out.push({ value: v, label: formatY(v) });
  }
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

function formatY(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  if (v >= 10) return v.toFixed(0);
  if (v >= 1) return v.toFixed(1);
  return v.toPrecision(2);
}

function validForAxis(y: number, axis: AxisType): boolean {
  if (!Number.isFinite(y)) return false;
  if (axis === "log" && y <= 0) return false;
  return true;
}

function pointsToPath(
  points: SeriesPoint[],
  xScale: (x: number) => number,
  yScale: (y: number) => number,
  axis: AxisType,
): string {
  const segments: string[] = [];
  let started = false;
  for (const p of points) {
    if (!validForAxis(p.y, axis)) {
      started = false;
      continue;
    }
    const cmd = started ? "L" : "M";
    segments.push(`${cmd}${xScale(p.t).toFixed(2)},${yScale(p.y).toFixed(2)}`);
    started = true;
  }
  return segments.join(" ");
}
