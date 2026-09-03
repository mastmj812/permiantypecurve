// Dossier gunbarrel — cross-section of one narvi scenario. X = narvi's
// per-leg gunbarrel_x_ft (perpendicular offset along the section axis),
// Y = target TVD (ft, increasing downward). The x-axis is oriented by
// the scenario's frame azimuth: ~N-S DSUs read W → E, ~E-W DSUs read
// N → S (see displayFrame). Symbology mirrors narvi's GunBarrel panel:
// PDP = solid circle, PUD = hollow circle, UPSIDE = hollow star;
// color = formation_blueox. U-turn legs at the same TVD are joined by
// a light link. Pure SVG (same approach as the other chart components
// — no plotting library).

import { useMemo } from "react";

import type { NarviWellGeo } from "../../api/narvi";
import { colorForFormation } from "../../map/formations";

interface Props {
  wells: NarviWellGeo[];
  // Scenario gunbarrel frame azimuth (axial, [0, 180)); null = legacy
  // save with no persisted frame — raw offsets, generic axis label.
  azimuthDeg: number | null;
  width: number;
  height: number;
}

interface Point {
  x: number; // offset ft
  tvd: number;
  color: string;
  category: string; // PDP / PUD / UPSIDE
  formation: string | null;
}

const PAD = { top: 16, right: 18, bottom: 50, left: 72 };

function niceTicks(min: number, max: number, target = 5): number[] {
  if (!(max > min)) return [min];
  const span = max - min;
  const step0 = span / target;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10]
    .map((m) => m * mag)
    .find((s) => span / s <= target) ?? 10 * mag;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step) {
    ticks.push(Math.round(t));
  }
  return ticks;
}

interface DisplayFrame {
  flip: boolean; // negate narvi's offsets for display
  left: string | null; // compass letter at the low-x end (null = unknown frame)
  right: string | null;
}

// narvi's +offset points 90° clockwise of the folded frame azimuth —
// compass EAST for a due-N-S lateral, SOUTH for a due-E-W one, but
// WEST-ish once the folded azimuth passes 135°. For display the axis
// always reads left→right as W → E when the DSU is ~N-S oriented
// (cross-section runs E-W) and N → S when it is ~E-W oriented, so the
// offsets' sign is flipped whenever narvi's +offset points against the
// reading direction. Display-only — persisted gunbarrel_x_ft values
// (and the dsu_meta frame Blue Ox receives) are untouched.
function displayFrame(azimuthDeg: number | null): DisplayFrame {
  if (azimuthDeg == null || !Number.isFinite(azimuthDeg)) {
    return { flip: false, left: null, right: null };
  }
  const a = (((azimuthDeg % 180) + 180) % 180) * (Math.PI / 180);
  const east = Math.cos(a); // compass components of narvi's +offset axis
  const south = Math.sin(a);
  if (Math.abs(east) >= Math.abs(south)) {
    return { flip: east < 0, left: "W", right: "E" };
  }
  return { flip: south < 0, left: "N", right: "S" };
}

function starPath(cx: number, cy: number, r: number): string {
  // 5-point star, point-up, inner radius 0.42r.
  const pts: string[] = [];
  for (let i = 0; i < 10; i++) {
    const rad = i % 2 === 0 ? r : r * 0.42;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    pts.push(`${(cx + rad * Math.cos(a)).toFixed(2)},${(cy + rad * Math.sin(a)).toFixed(2)}`);
  }
  return `M${pts.join("L")}Z`;
}

export function ScenarioGunBarrel({ wells, azimuthDeg, width, height }: Props) {
  const frame = displayFrame(azimuthDeg);
  const { points, links, formations, skipped } = useMemo(() => {
    const sgn = frame.flip ? -1 : 1;
    const pts: Point[] = [];
    const lks: Array<{ x1: number; x2: number; tvd: number }> = [];
    let skippedWells = 0;
    for (const w of wells) {
      if (w.target_tvd_ft == null || w.gunbarrel_xs.length === 0) {
        skippedWells += 1;
        continue;
      }
      const color = colorForFormation(w.formation);
      for (const x of w.gunbarrel_xs) {
        pts.push({
          x: sgn * x,
          tvd: w.target_tvd_ft,
          color,
          category: w.category,
          formation: w.formation,
        });
      }
      if (w.gunbarrel_xs.length >= 2) {
        const xs = w.gunbarrel_xs.map((x) => sgn * x).sort((a, b) => a - b);
        lks.push({ x1: xs[0]!, x2: xs[xs.length - 1]!, tvd: w.target_tvd_ft });
      }
    }
    const fms = [...new Set(pts.map((p) => p.formation ?? "(none)"))];
    return { points: pts, links: lks, formations: fms, skipped: skippedWells };
  }, [wells, frame.flip]);

  if (points.length === 0) {
    return (
      <div style={{ width, height, display: "grid", placeItems: "center" }}>
        <span className="muted">
          no TVD / offset data for this scenario
        </span>
      </div>
    );
  }

  const xs = points.map((p) => p.x);
  const tvds = points.map((p) => p.tvd);
  const xSpan = Math.max(Math.max(...xs) - Math.min(...xs), 500);
  const xMin = Math.min(...xs) - 0.08 * xSpan;
  const xMax = Math.max(...xs) + 0.08 * xSpan;
  const tvdSpan = Math.max(Math.max(...tvds) - Math.min(...tvds), 200);
  const tvdMin = Math.min(...tvds) - 0.12 * tvdSpan;
  const tvdMax = Math.max(...tvds) + 0.12 * tvdSpan;

  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const sx = (x: number) => PAD.left + ((x - xMin) / (xMax - xMin)) * plotW;
  // TVD increases DOWNWARD: shallow at the top of the plot.
  const sy = (tvd: number) => PAD.top + ((tvd - tvdMin) / (tvdMax - tvdMin)) * plotH;

  const xTicks = niceTicks(xMin, xMax);
  const yTicks = niceTicks(tvdMin, tvdMax);

  return (
    <svg
      width={width}
      height={height}
      style={{ background: "#ffffff", display: "block" }}
    >
      {/* axes + grid */}
      {yTicks.map((t) => (
        <g key={`y${t}`}>
          <line
            x1={PAD.left} x2={width - PAD.right} y1={sy(t)} y2={sy(t)}
            stroke="#e5e7eb" strokeWidth={1}
          />
          <text
            x={PAD.left - 6} y={sy(t) + 4} textAnchor="end"
            fontSize={12} fill="#6b7280"
          >
            {t.toLocaleString()}
          </text>
        </g>
      ))}
      {xTicks.map((t) => (
        <g key={`x${t}`}>
          <line
            x1={sx(t)} x2={sx(t)} y1={PAD.top} y2={height - PAD.bottom}
            stroke="#f3f4f6" strokeWidth={1}
          />
          <text
            x={sx(t)} y={height - PAD.bottom + 15} textAnchor="middle"
            fontSize={12} fill="#6b7280"
          >
            {t.toLocaleString()}
          </text>
        </g>
      ))}
      <line
        x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={height - PAD.bottom}
        stroke="#9ca3af" strokeWidth={1}
      />
      <line
        x1={PAD.left} x2={width - PAD.right}
        y1={height - PAD.bottom} y2={height - PAD.bottom}
        stroke="#9ca3af" strokeWidth={1}
      />
      <text
        x={PAD.left + plotW / 2} y={height - 6} textAnchor="middle"
        fontSize={12} fill="#374151"
      >
        {frame.left
          ? `cross-section offset (ft) — ${frame.left} → ${frame.right}`
          : "cross-section offset (ft)"}
      </text>
      {frame.left && (
        <>
          <text
            x={PAD.left} y={height - 6} textAnchor="start"
            fontSize={12} fontWeight={600} fill="#374151"
          >
            {frame.left}
          </text>
          <text
            x={width - PAD.right} y={height - 6} textAnchor="end"
            fontSize={12} fontWeight={600} fill="#374151"
          >
            {frame.right}
          </text>
        </>
      )}
      <text
        x={12} y={PAD.top + plotH / 2} fontSize={12} fill="#374151"
        transform={`rotate(-90 12 ${PAD.top + plotH / 2})`}
        textAnchor="middle"
      >
        TVD (ft)
      </text>

      {/* U-turn leg links under the markers */}
      {links.map((l, i) => (
        <line
          key={i}
          x1={sx(l.x1)} x2={sx(l.x2)} y1={sy(l.tvd)} y2={sy(l.tvd)}
          stroke="#9ca3af" strokeWidth={1.2} strokeDasharray="3 2"
        />
      ))}

      {/* wells */}
      {points.map((p, i) => {
        const cx = sx(p.x);
        const cy = sy(p.tvd);
        if (p.category === "PDP") {
          return <circle key={i} cx={cx} cy={cy} r={5} fill={p.color} />;
        }
        if (p.category === "UPSIDE") {
          return (
            <path
              key={i}
              d={starPath(cx, cy, 7)}
              fill="#ffffff"
              stroke={p.color}
              strokeWidth={1.8}
            />
          );
        }
        // PUD
        return (
          <circle
            key={i} cx={cx} cy={cy} r={5}
            fill="#ffffff" stroke={p.color} strokeWidth={2}
          />
        );
      })}

      {/* legend: formation chips + symbology */}
      <g transform={`translate(${PAD.left}, ${height - PAD.bottom + 30})`}>
        {formations.map((f, i) => (
          <g key={f} transform={`translate(${i * 92}, 0)`}>
            <rect
              x={0} y={-9} width={11} height={11} rx={2}
              fill={colorForFormation(f === "(none)" ? null : f)}
            />
            <text x={15} y={1} fontSize={12} fill="#374151">{f}</text>
          </g>
        ))}
        <text
          x={plotW} y={1} textAnchor="end" fontSize={12} fill="#6b7280"
        >
          {`PDP ● · PUD ○ · UPSIDE ☆${skipped > 0 ? ` · ${skipped} well(s) without TVD omitted` : ""}`}
        </text>
      </g>
    </svg>
  );
}
