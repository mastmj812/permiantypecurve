// Semi-log daily decline chart for the PDP review tab. Hand-rolled SVG
// (same rationale as forecasts/DeclineChart.tsx), but on a CALENDAR
// x-axis: daily actuals live on dates, and the fit's time origin
// (the stream's own peak) and the engineer's fit window are dates too.
//
//   * dots           producing-day actuals (down days are not rates)
//   * solid line     model over the fitted history (anchor -> data_through)
//   * dashed line    forecast (data_through ->)
//   * grey span      shut-in events (>= 14 d, excluded from uptime)
//   * purple dashes  choke changes (faint = early-life managed ramp)
//   * amber line     engineer fit-window start
//   * grey line      anchor (t = 0 of the params)
//
// Model and actuals are both PRODUCING-DAY rates; uptime is applied only
// to volumes, so the curve should sit on the dots, not under them.
// Click anywhere in the plot -> onPickDate(YYYY-MM-DD).

import { useMemo } from "react";

import { STREAM_COLOR, type PdpSeries, type PdpStream } from "../api/pdp";

const PAD = { top: 14, right: 16, bottom: 40, left: 64 };
const DAY_MS = 86_400_000;

const UNITS: Record<PdpStream, string> = {
  oil: "BOPD",
  gas: "MCFD",
  water: "BWPD",
};

interface Props {
  series: PdpSeries;
  width?: number;
  height?: number;
  onPickDate?: (iso: string) => void;
}

function ts(iso: string): number {
  return Date.parse(`${iso}T00:00:00Z`);
}

function isoOf(t: number): string {
  return new Date(t).toISOString().slice(0, 10);
}

export function PdpChart({
  series,
  width = 860,
  height = 360,
  onPickDate,
}: Props) {
  const color = STREAM_COLOR[series.stream];
  const plot = {
    x: PAD.left,
    y: PAD.top,
    w: width - PAD.left - PAD.right,
    h: height - PAD.top - PAD.bottom,
  };

  const geom = useMemo(() => {
    const pts = series.actual.filter(
      (p) => !p.down && p.q !== null && p.q > 0,
    ) as Array<{
      d: string;
      q: number;
    }>;
    const model = series.model.filter((p) => p.q > 0);
    const t0 = ts(series.actual[0]?.d ?? series.anchor_date);
    const t1 = Math.max(ts(series.data_through), ...model.map((p) => ts(p.d)));
    const qs = [...pts.map((p) => p.q), ...model.map((p) => p.q)];
    const qMax = Math.max(10, ...qs);
    // Down to the lowest plotted value, but never more than 3 decades
    // under the max (a 50-yr tail would otherwise flatten the history).
    const qMin = Math.max(0.5, qMax / 1000, Math.min(...qs));
    const lo = Math.floor(Math.log10(qMin));
    const hi = Math.ceil(Math.log10(qMax));
    const x = (t: number) =>
      plot.x + ((t - t0) / Math.max(t1 - t0, DAY_MS)) * plot.w;
    const y = (q: number) =>
      plot.y +
      plot.h -
      ((Math.log10(Math.max(q, 10 ** lo)) - lo) / Math.max(hi - lo, 1)) *
        plot.h;
    const yTicks: number[] = [];
    for (let e = lo; e <= hi; e++) yTicks.push(10 ** e);
    const xTicks: number[] = [];
    const y0 = new Date(t0).getUTCFullYear();
    const y1 = new Date(t1).getUTCFullYear();
    const step = y1 - y0 > 10 ? 2 : 1;
    for (let yr = y0 + 1; yr <= y1; yr += step) xTicks.push(Date.UTC(yr, 0, 1));
    const through = ts(series.data_through);
    const hist = model.filter((p) => ts(p.d) <= through);
    const fwd = model.filter((p) => ts(p.d) >= through);
    const path = (arr: Array<{ d: string; q: number }>) =>
      arr
        .map(
          (p, i) =>
            `${i ? "L" : "M"}${x(ts(p.d)).toFixed(1)},${y(p.q).toFixed(1)}`,
        )
        .join("");
    return {
      pts,
      x,
      y,
      t0,
      t1,
      yTicks,
      xTicks,
      histPath: path(hist),
      fwdPath: path(fwd),
    };
  }, [series, plot.x, plot.y, plot.w, plot.h]);

  const vline = (
    iso: string | null,
    stroke: string,
    dash?: string,
    title?: string,
    opacity = 1,
  ) =>
    iso === null ? null : (
      <line
        x1={geom.x(ts(iso))}
        x2={geom.x(ts(iso))}
        y1={plot.y}
        y2={plot.y + plot.h}
        stroke={stroke}
        strokeDasharray={dash}
        strokeWidth={1.25}
        opacity={opacity}
      >
        {title ? <title>{title}</title> : null}
      </line>
    );

  const onClick = (e: React.MouseEvent<SVGRectElement>) => {
    if (!onPickDate) return;
    const box = e.currentTarget.getBoundingClientRect();
    const frac = (e.clientX - box.left) / box.width;
    onPickDate(isoOf(geom.t0 + frac * (geom.t1 - geom.t0)));
  };

  return (
    <svg
      width={width}
      height={height}
      className="decline-chart"
      role="img"
      aria-label={`${series.stream} daily rate`}
    >
      <rect
        x={plot.x}
        y={plot.y}
        width={plot.w}
        height={plot.h}
        fill="#fff"
        stroke="#e5e7eb"
      />
      {geom.yTicks.map((q) => (
        <g key={`y${q}`}>
          <line
            x1={plot.x}
            x2={plot.x + plot.w}
            y1={geom.y(q)}
            y2={geom.y(q)}
            stroke="#f3f4f6"
          />
          <text
            x={plot.x - 6}
            y={geom.y(q)}
            textAnchor="end"
            dominantBaseline="middle"
            fontSize="12"
            fill="#6b7280"
          >
            {q >= 1 ? q.toLocaleString() : q}
          </text>
        </g>
      ))}
      {geom.xTicks.map((t) => (
        <g key={`x${t}`}>
          <line
            x1={geom.x(t)}
            x2={geom.x(t)}
            y1={plot.y}
            y2={plot.y + plot.h}
            stroke="#f3f4f6"
          />
          <text
            x={geom.x(t)}
            y={plot.y + plot.h + 14}
            textAnchor="middle"
            fontSize="12"
            fill="#6b7280"
          >
            {new Date(t).getUTCFullYear()}
          </text>
        </g>
      ))}
      {series.events.map((ev) => (
        <rect
          key={`ev${ev.start}`}
          x={geom.x(ts(ev.start))}
          y={plot.y}
          width={Math.max(
            1,
            geom.x(ts(ev.end) + DAY_MS) - geom.x(ts(ev.start)),
          )}
          height={plot.h}
          fill="#9ca3af"
          opacity={0.18}
        >
          <title>{`shut-in ${ev.start} → ${ev.end}`}</title>
        </rect>
      ))}
      {series.breaks
        .filter((b) => b.kind === "choke_change")
        .map((b) => (
          <g key={`ch${b.start}`}>
            {vline(
              b.start,
              "#7c3aed",
              "3 3",
              `choke ${b.choke_from}→${b.choke_to} on ${b.start}` +
                (b.rate_ratio ? ` (oil x${b.rate_ratio.toFixed(2)})` : "") +
                (b.early_life ? " — early-life ramp" : ""),
              b.early_life || b.material === false ? 0.2 : 0.85,
            )}
          </g>
        ))}
      {vline(
        series.anchor_date,
        "#9ca3af",
        "1 3",
        `anchor (t = 0): ${series.anchor_date}`,
      )}
      {vline(
        series.data_through,
        "#374151",
        "2 2",
        `data through ${series.data_through}`,
      )}
      {vline(
        series.fit_start_date,
        "#d97706",
        undefined,
        `fit window starts ${series.fit_start_date ?? ""}`,
      )}
      {geom.pts.map((p) => (
        <circle
          key={p.d}
          cx={geom.x(ts(p.d))}
          cy={geom.y(p.q)}
          r={1.6}
          fill={color}
          opacity={0.45}
        />
      ))}
      <path d={geom.histPath} fill="none" stroke="#111827" strokeWidth={1.75} />
      <path
        d={geom.fwdPath}
        fill="none"
        stroke="#111827"
        strokeWidth={1.75}
        strokeDasharray="6 4"
      />
      {/* Transparent hit area on top for click-to-pick. */}
      <rect
        x={plot.x}
        y={plot.y}
        width={plot.w}
        height={plot.h}
        fill="transparent"
        style={{ cursor: onPickDate ? "crosshair" : "default" }}
        onClick={onClick}
      />
      <text
        x={12}
        y={plot.y + plot.h / 2}
        transform={`rotate(-90 12 ${plot.y + plot.h / 2})`}
        textAnchor="middle"
        fontSize="13"
        fill="#374151"
      >
        {`${series.stream} — producing-day ${UNITS[series.stream]} (log)`}
      </text>
    </svg>
  );
}
