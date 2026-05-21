// Pure-SVG probit plot of per-well oil EUR / ft.
//
// X axis: log10(EUR/ft) in BBL/ft. Y axis: probit (inverse normal of the
// cumulative probability), with right-side labels at P1/P10/P25/P50/
// P75/P90/P99. Wells render as green dots, sorted ascending; the gray
// line is the OLS fit of probit vs log10(EUR/ft) — equivalent to a
// lognormal fit. Vertical lines mark the type curve's EUR/ft (black),
// the well-population arithmetic mean (orange dashed), and the
// previous TC's EUR/ft (gray dotted) when comparing.
//
// All math is local: a six-constant Beasley-Springer probit
// approximation, an OLS slope/intercept on (log_eur, probit), and the
// label probit anchors at p = 1 - X/100 to match operator convention
// (P1 = top 1% well = highest EUR).

import type { TypeCurveWellStat } from "../../api/typeCurves";
import type { TypeCurveRow } from "../../api/typeCurves";

interface Props {
  stats: TypeCurveWellStat[];
  current: TypeCurveRow;
  previous: TypeCurveRow | null;
  width?: number;
  height?: number;
}

const PAD = { top: 22, right: 64, bottom: 44, left: 60 };
const RIGHT_AXIS_LABELS: Array<{ label: string; pct: number }> = [
  { label: "P1", pct: 0.01 },
  { label: "P10", pct: 0.10 },
  { label: "P25", pct: 0.25 },
  { label: "P50", pct: 0.50 },
  { label: "P75", pct: 0.75 },
  { label: "P90", pct: 0.90 },
  { label: "P99", pct: 0.99 },
];

// Beasley-Springer-Moro probit (inverse normal CDF). ~4-decimal
// accuracy across (0, 1), which is more than the chart needs.
function probit(p: number): number {
  if (!Number.isFinite(p) || p <= 0) return -6;
  if (p >= 1) return 6;
  const a = [
    -3.969683028665376e1, 2.209460984245205e2, -2.759285104469687e2,
    1.38357751867269e2, -3.066479806614716e1, 2.506628277459239,
  ] as const;
  const b = [
    -5.447609879822406e1, 1.615858368580409e2, -1.556989798598866e2,
    6.680131188771972e1, -1.328068155288572e1,
  ] as const;
  const c = [
    -7.784894002430293e-3, -3.223964580411365e-1, -2.400758277161838,
    -2.549732539343734, 4.374664141464968, 2.938163982698783,
  ] as const;
  const d = [
    7.784695709041462e-3, 3.224671290700398e-1, 2.445134137142996,
    3.754408661907416,
  ] as const;
  const pLow = 0.02425;
  const pHigh = 1 - pLow;
  let q: number;
  let r: number;
  if (p < pLow) {
    q = Math.sqrt(-2 * Math.log(p));
    return (
      (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    );
  }
  if (p <= pHigh) {
    q = p - 0.5;
    r = q * q;
    return (
      (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) *
      q /
      (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    );
  }
  q = Math.sqrt(-2 * Math.log(1 - p));
  return -(
    (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
    ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
  );
}

function tcEurPerFt(curve: TypeCurveRow | null): number | null {
  if (!curve) return null;
  const fitted = (
    curve.series as { streams?: { oil?: { fitted?: { eur_per_unit?: number } | null } } }
  ).streams?.oil?.fitted;
  const v = fitted?.eur_per_unit;
  // eur_per_unit is per 1,000 ft (per the saved-curve basis comment) —
  // divide by 1,000 to land in BBL/ft, matching wells.oil_eur_per_ft.
  return v != null && Number.isFinite(v) && v > 0 ? v / 1000 : null;
}

export function SlideProbit({
  stats,
  current,
  previous,
  width = 528,
  height = 240,
}: Props) {
  // Drop wells without a well-stats EUR/ft (no oil forecast or missing
  // lateral). Probit only plots the population we actually have.
  const valid = stats
    .filter((s) => s.oil_eur_per_ft != null && s.oil_eur_per_ft > 0)
    .sort((a, b) => (a.oil_eur_per_ft as number) - (b.oil_eur_per_ft as number));
  const n = valid.length;

  if (n < 3) {
    return (
      <svg width={width} height={height} className="slide-probit-empty">
        <rect
          x={0}
          y={0}
          width={width}
          height={height}
          fill="#fff"
          stroke="#e5e7eb"
        />
        <text
          x={width / 2}
          y={height / 2}
          textAnchor="middle"
          fontSize="12"
          fill="#6b7280"
        >
          Need ≥3 wells with EUR/ft for probit ({n} available)
        </text>
      </svg>
    );
  }

  const tcVal = tcEurPerFt(current);
  const prevVal = tcEurPerFt(previous);
  const wellMean =
    valid.reduce((s, w) => s + (w.oil_eur_per_ft as number), 0) / n;

  const eurs = valid.map((w) => w.oil_eur_per_ft as number);
  const xMin = Math.min(...eurs, tcVal ?? Infinity, prevVal ?? Infinity, wellMean);
  const xMax = Math.max(...eurs, tcVal ?? -Infinity, prevVal ?? -Infinity, wellMean);
  // Pad the x-domain by ~10% on each side in log-space so the fit line
  // doesn't terminate exactly at the extreme dots.
  const logMin = Math.log10(xMin) - 0.04;
  const logMax = Math.log10(xMax) + 0.04;
  const yMin = probit(0.005);
  const yMax = probit(0.995);

  const plotW = width - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;
  const xScale = (eur: number) =>
    PAD.left + ((Math.log10(eur) - logMin) / (logMax - logMin)) * plotW;
  const yScale = (z: number) =>
    PAD.top + plotH - ((z - yMin) / (yMax - yMin)) * plotH;

  // Per-well probit positions: cumulative probability = (i + 0.5) / n.
  const dots = valid.map((w, i) => {
    const cum = (i + 0.5) / n;
    return {
      api14: w.api14,
      x: xScale(w.oil_eur_per_ft as number),
      y: yScale(probit(cum)),
    };
  });

  // OLS fit of probit on log10(eur). Doubling as the lognormal fit.
  const xs = valid.map((w) => Math.log10(w.oil_eur_per_ft as number));
  const ys = valid.map((_, i) => probit((i + 0.5) / n));
  const meanX = xs.reduce((s, v) => s + v, 0) / n;
  const meanY = ys.reduce((s, v) => s + v, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) {
    const xi = xs[i]!;
    const yi = ys[i]!;
    num += (xi - meanX) * (yi - meanY);
    den += (xi - meanX) ** 2;
  }
  const slope = den > 0 ? num / den : 0;
  const intercept = meanY - slope * meanX;
  const fitY = (lEur: number) => slope * lEur + intercept;
  // Fit line endpoints clamped to the plot rectangle so it doesn't run
  // off the top/bottom into the labels.
  const fitX1 = logMin;
  const fitX2 = logMax;
  const fitYClampedSvg = (lx: number) => {
    const zy = fitY(lx);
    const zyClamped = Math.max(yMin, Math.min(yMax, zy));
    return yScale(zyClamped);
  };

  // X-axis ticks: pick 5-7 round-ish values in log space.
  const xTicks = pickLogTicks(Math.pow(10, logMin), Math.pow(10, logMax));

  return (
    <svg width={width} height={height} className="slide-probit">
      {/* Plot frame */}
      <rect
        x={PAD.left}
        y={PAD.top}
        width={plotW}
        height={plotH}
        fill="#fff"
        stroke="#9ca3af"
      />

      {/* Horizontal gridlines at each P label */}
      {RIGHT_AXIS_LABELS.map((lab) => {
        // P_X = top X% well = exceedance prob X/100 = cum prob 1 - X/100.
        const cum = 1 - lab.pct;
        const z = probit(cum);
        const y = yScale(z);
        if (y < PAD.top - 1 || y > PAD.top + plotH + 1) return null;
        return (
          <g key={lab.label}>
            <line
              x1={PAD.left}
              x2={PAD.left + plotW}
              y1={y}
              y2={y}
              stroke="#e5e7eb"
            />
            <text
              x={PAD.left + plotW + 6}
              y={y}
              dominantBaseline="middle"
              fontSize="10"
              fill="#6b7280"
            >
              {lab.label}
            </text>
          </g>
        );
      })}

      {/* X-axis ticks + labels */}
      {xTicks.map((t) => (
        <g key={`x${t}`}>
          <line
            x1={xScale(t)}
            x2={xScale(t)}
            y1={PAD.top}
            y2={PAD.top + plotH}
            stroke="#f3f4f6"
          />
          <text
            x={xScale(t)}
            y={PAD.top + plotH + 14}
            textAnchor="middle"
            fontSize="10"
            fill="#6b7280"
          >
            {fmtTick(t)}
          </text>
        </g>
      ))}

      {/* OLS fit line */}
      <line
        x1={xScale(Math.pow(10, fitX1))}
        y1={fitYClampedSvg(fitX1)}
        x2={xScale(Math.pow(10, fitX2))}
        y2={fitYClampedSvg(fitX2)}
        stroke="#6b7280"
        strokeWidth={1}
      />

      {/* Previous TC vertical (dotted gray) */}
      {prevVal != null && (
        <line
          x1={xScale(prevVal)}
          x2={xScale(prevVal)}
          y1={PAD.top}
          y2={PAD.top + plotH}
          stroke="#6b7280"
          strokeWidth={1.5}
          strokeDasharray="2 3"
        />
      )}

      {/* Wells average vertical (orange dashed) */}
      <line
        x1={xScale(wellMean)}
        x2={xScale(wellMean)}
        y1={PAD.top}
        y2={PAD.top + plotH}
        stroke="#f59e0b"
        strokeWidth={1.5}
        strokeDasharray="6 3"
      />

      {/* Type Curve vertical (solid black) */}
      {tcVal != null && (
        <line
          x1={xScale(tcVal)}
          x2={xScale(tcVal)}
          y1={PAD.top}
          y2={PAD.top + plotH}
          stroke="#0f172a"
          strokeWidth={2}
        />
      )}

      {/* Per-well green dots */}
      {dots.map((d) => (
        <circle
          key={d.api14}
          cx={d.x}
          cy={d.y}
          r={4}
          fill="#16a34a"
          stroke="#064e3b"
          strokeWidth={0.5}
        />
      ))}

      {/* Axis labels */}
      <text
        x={PAD.left + plotW / 2}
        y={height - 4}
        textAnchor="middle"
        fontSize="11"
        fill="#374151"
      >
        Oil EUR / FT (BBL/ft)
      </text>

      {/* Legend, top-left */}
      <g transform={`translate(${PAD.left + 8}, ${PAD.top + 8})`}>
        <rect width="120" height="74" fill="#ffffff" stroke="#e5e7eb" />
        <circle cx={10} cy={12} r={4} fill="#16a34a" />
        <text x={20} y={15} fontSize="10" fill="#374151">
          Wells
        </text>
        <line x1={4} y1={28} x2={16} y2={28} stroke="#6b7280" strokeWidth={1} />
        <text x={20} y={31} fontSize="10" fill="#374151">
          Probit Fit
        </text>
        <line
          x1={4}
          y1={44}
          x2={16}
          y2={44}
          stroke="#f59e0b"
          strokeWidth={1.5}
          strokeDasharray="3 2"
        />
        <text x={20} y={47} fontSize="10" fill="#374151">
          Wells Avg
        </text>
        <line x1={4} y1={60} x2={16} y2={60} stroke="#0f172a" strokeWidth={2} />
        <text x={20} y={63} fontSize="10" fill="#374151">
          Type Curve
        </text>
        {previous && (
          <>
            <line
              x1={4}
              y1={76}
              x2={16}
              y2={76}
              stroke="#6b7280"
              strokeWidth={1.5}
              strokeDasharray="2 3"
            />
            <text x={20} y={79} fontSize="10" fill="#374151">
              Previous TC
            </text>
          </>
        )}
      </g>
    </svg>
  );
}

function pickLogTicks(min: number, max: number): number[] {
  // Generate 4-7 "nice" ticks across the log-x range. For typical Permian
  // EUR/ft (50–150 BBL/ft) the obvious step is 10. The fallback handles
  // very narrow or very wide ranges.
  const span = max / Math.max(min, 1e-9);
  if (span <= 0 || !Number.isFinite(span)) return [];
  let step: number;
  if (max - min <= 60) step = 10;
  else if (max - min <= 150) step = 25;
  else step = Math.pow(10, Math.floor(Math.log10(max - min) - 0.5));
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + 1e-9; v += step) {
    out.push(Math.round(v * 100) / 100);
    if (out.length > 8) break;
  }
  return out;
}

function fmtTick(v: number): string {
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  if (v >= 10) return v.toFixed(0);
  return v.toFixed(1);
}
