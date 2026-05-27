// Tiny inline SVG sparkline for the forecast grid. History as dark dots,
// forecast as a light dashed line. No axes.

import type { SeriesPoint } from "./DeclineChart";

interface Props {
  history: SeriesPoint[];
  forecast: SeriesPoint[];
  width?: number;
  height?: number;
}

export function Sparkline({ history, forecast, width = 120, height = 28 }: Props) {
  const all = [...history, ...forecast];
  if (all.length === 0) return <svg width={width} height={height} />;
  const xMax = Math.max(...all.map((p) => p.t)) || 1;
  const yMax = Math.max(...all.map((p) => p.y)) || 1;

  const x = (t: number) => (t / xMax) * (width - 4) + 2;
  const y = (v: number) => height - 2 - (v / yMax) * (height - 4);

  const histPath = pathFrom(history, x, y);
  const fcPath = pathFrom(forecast, x, y);

  return (
    <svg width={width} height={height}>
      {fcPath && (
        <path d={fcPath} fill="none" stroke="#dc2626" strokeWidth={1} strokeDasharray="2 2" />
      )}
      {histPath && <path d={histPath} fill="none" stroke="#1f2937" strokeWidth={1} />}
    </svg>
  );
}

function pathFrom(
  points: SeriesPoint[],
  x: (t: number) => number,
  y: (v: number) => number,
): string | null {
  if (points.length < 2) return null;
  return points
    .map((p, i) => `${i === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.y).toFixed(1)}`)
    .join(" ");
}
