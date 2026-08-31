// Probit / lognormal-fit plot for the Type Curve tab.
//
// Two data sources depending on context:
//   * Saved TC selected (typeCurveId set) → fetch the workspace's
//     per-well payloads via the same /wells endpoint the workspace
//     uses. That goes through resolve_forecast(), so any TC-scoped
//     overrides the engineer wrote on the wells page flow into the
//     probit dots.
//   * Live preview (typeCurveId null) → fall back to the global
//     forecasts table via listForecasts(). No overrides can exist
//     yet since no TC has been saved.
//
// Either way, each well's EUR/ft is recomputed via the monthly
// trapezoid sum (same convention as the Review tab + the slide
// export). The published TC's EUR/ft renders as a vertical
// reference line.

import { useEffect, useMemo, useState } from "react";

import { listForecasts, type ForecastRow, type Stream } from "../api/forecasts";
import {
  fetchTypeCurveWorkspaceWells,
  type WorkspaceWell,
} from "../api/typeCurves";
import { eurFromForecastParams } from "../forecasts/arps";

interface Props {
  api10s: string[];
  // BBL/MCF per 1,000 lateral ft for the displayed fit's 50-yr EUR.
  // Null when no fit is available (e.g. the cohort failed to fit).
  tcEurPerUnit: number | null;
  // Same convention for the "Compare with" curve, when set.
  prevEurPerUnit?: number | null;
  prevLabel?: string | null;
  stream?: Stream;
  // When a saved TC is selected, this is its id — the probit then
  // pulls override-aware per-well payloads. Null on the live-preview
  // path (cohort lassoed on the map but not saved yet).
  typeCurveId?: string | null;
  width?: number;
  height?: number;
}

const PAD = { top: 22, right: 64, bottom: 44, left: 60 };
// SPE / PRMS reserves convention: Pxx = the value exceeded by xx% of
// wells, so P10 is the HIGH case and P90 the LOW case. The dots are
// plotted at cumulative-from-below probability, so label Pxx sits at
// pct = 1 - xx/100 (P10 → 0.90, i.e. the top of the distribution).
const RIGHT_AXIS_LABELS: Array<{ label: string; pct: number }> = [
  { label: "P99", pct: 0.01 },
  { label: "P90", pct: 0.10 },
  { label: "P75", pct: 0.25 },
  { label: "P50", pct: 0.50 },
  { label: "P25", pct: 0.75 },
  { label: "P10", pct: 0.90 },
  { label: "P1", pct: 0.99 },
];

// Beasley-Springer-Moro probit (inverse normal CDF). ~4-decimal accuracy.
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

export function TypeCurveProbit({
  api10s,
  tcEurPerUnit,
  prevEurPerUnit = null,
  prevLabel = null,
  stream = "oil",
  typeCurveId = null,
  width = 640,
  height = 280,
}: Props) {
  // Two parallel data slots — only one is non-null at a time depending
  // on whether we're in saved-TC or live-preview mode (see header).
  const [wsWells, setWsWells] = useState<WorkspaceWell[] | null>(null);
  const [forecasts, setForecasts] = useState<ForecastRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-fetch when the cohort changes. Key on the joined api10s string so
  // React can compare cheaply (the array reference changes every parent
  // render even when the contents are the same).
  const api10sKey = api10s.join(",");
  useEffect(() => {
    if (api10s.length === 0) {
      setWsWells([]);
      setForecasts([]);
      return;
    }
    let cancelled = false;
    setError(null);
    if (typeCurveId) {
      // Saved TC: workspace endpoint resolves overrides per stream.
      setForecasts(null);
      fetchTypeCurveWorkspaceWells(typeCurveId)
        .then((rows) => {
          if (!cancelled) setWsWells(rows);
        })
        .catch((e) => {
          if (!cancelled) setError(e instanceof Error ? e.message : String(e));
        });
    } else {
      // Live preview: fall back to global forecasts (no overrides
      // exist yet — the TC isn't saved).
      setWsWells(null);
      listForecasts(api10s)
        .then((rows) => {
          if (!cancelled) setForecasts(rows);
        })
        .catch((e) => {
          if (!cancelled) setError(e instanceof Error ? e.message : String(e));
        });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api10sKey, typeCurveId]);

  // Per-well EUR/ft for the current stream, computed via the monthly
  // trapezoid sum (same as the Review tab + the slide's well-stats).
  // EUR/ft units: BBL/ft for oil/water, MCF/ft for gas.
  const wellEurPerFt = useMemo(() => {
    // Saved-TC path: pull params from the per-stream resolved payload
    // on each WorkspaceWell. The payload's `params` mirror the global
    // forecast's `params` shape so `eurFromForecastParams` works as-is.
    if (wsWells) {
      const out: number[] = [];
      for (const w of wsWells) {
        const lat = w.well_lateral_ft;
        if (lat == null || lat <= 0) continue;
        const slot = w[stream];
        const payload = slot?.payload;
        const params = (payload?.params as Record<string, number> | undefined) ?? null;
        if (!params) continue;
        const eur = eurFromForecastParams(params);
        if (eur == null || !Number.isFinite(eur) || eur <= 0) continue;
        out.push(eur / lat);
      }
      return out;
    }
    if (forecasts) {
      const out: number[] = [];
      for (const f of forecasts) {
        if (f.stream !== stream) continue;
        const lat = f.well_lateral_ft;
        if (lat == null || lat <= 0) continue;
        const eur = eurFromForecastParams(f.params);
        if (eur == null || !Number.isFinite(eur) || eur <= 0) continue;
        out.push(eur / lat);
      }
      return out;
    }
    return null;
  }, [wsWells, forecasts, stream]);

  // TC EUR/ft = eur_per_unit (per 1k ft) / 1000.
  const tcVal = tcEurPerUnit != null && tcEurPerUnit > 0 ? tcEurPerUnit / 1000 : null;
  const prevVal =
    prevEurPerUnit != null && prevEurPerUnit > 0 ? prevEurPerUnit / 1000 : null;

  if (error) {
    return (
      <div className="probit-empty" style={{ width, height }}>
        Failed to load probit data: {error}
      </div>
    );
  }

  if (wellEurPerFt == null) {
    return (
      <div className="probit-empty" style={{ width, height }}>
        Loading…
      </div>
    );
  }

  if (wellEurPerFt.length < 3) {
    return (
      <svg width={width} height={height} className="probit-svg">
        <rect x={0} y={0} width={width} height={height} fill="#fff" stroke="#e5e7eb" />
        <text
          x={width / 2}
          y={height / 2}
          textAnchor="middle"
          fontSize="14"
          fill="#6b7280"
        >
          Need ≥3 wells with valid EUR/ft for probit ({wellEurPerFt.length} available)
        </text>
      </svg>
    );
  }

  const sorted = [...wellEurPerFt].sort((a, b) => a - b);
  const n = sorted.length;
  const wellMean = sorted.reduce((s, v) => s + v, 0) / n;
  // Cohort median EUR/ft — the operator-convention P50 reserves
  // target. The TC vertical SHOULD land near this on a well-fit
  // curve; if it sits far to the right, the TC is biased high
  // (typically because the fitter chased the rate-mean line on
  // the data-window charts).
  const wellMedian =
    n % 2 === 1
      ? sorted[Math.floor(n / 2)]!
      : (sorted[n / 2 - 1]! + sorted[n / 2]!) / 2;

  const xMin = Math.min(
    ...sorted,
    tcVal ?? Infinity, prevVal ?? Infinity, wellMean, wellMedian,
  );
  const xMax = Math.max(
    ...sorted,
    tcVal ?? -Infinity, prevVal ?? -Infinity, wellMean, wellMedian,
  );
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

  const dots = sorted.map((eur, i) => {
    const cum = (i + 0.5) / n;
    return { x: xScale(eur), y: yScale(probit(cum)), key: `dot-${i}` };
  });

  // OLS fit of probit on log10(eur) — i.e. the lognormal fit.
  const xs = sorted.map((v) => Math.log10(v));
  const ys = sorted.map((_, i) => probit((i + 0.5) / n));
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
  const fitYClampedSvg = (lx: number) => {
    const zy = slope * lx + intercept;
    return yScale(Math.max(yMin, Math.min(yMax, zy)));
  };

  const xTicks = pickLogTicks(Math.pow(10, logMin), Math.pow(10, logMax));
  const unitLabel = stream === "gas" ? "MCF/ft" : "BBL/ft";

  return (
    <svg width={width} height={height} className="probit-svg">
      <rect
        x={PAD.left}
        y={PAD.top}
        width={plotW}
        height={plotH}
        fill="#fff"
        stroke="#9ca3af"
      />

      {RIGHT_AXIS_LABELS.map((lab) => {
        const z = probit(1 - lab.pct);
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
              fontSize="12"
              fill="#6b7280"
            >
              {lab.label}
            </text>
          </g>
        );
      })}

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
            fontSize="12"
            fill="#6b7280"
          >
            {fmtTick(t)}
          </text>
        </g>
      ))}

      <line
        x1={xScale(Math.pow(10, logMin))}
        y1={fitYClampedSvg(logMin)}
        x2={xScale(Math.pow(10, logMax))}
        y2={fitYClampedSvg(logMax)}
        stroke="#6b7280"
        strokeWidth={1}
      />

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

      {/* Cohort MEDIAN per-well EUR — the operator-convention P50
          target the published TC should sit near. Solid thin orange
          so it visually contrasts with the dashed mean below it. */}
      <line
        x1={xScale(wellMedian)}
        x2={xScale(wellMedian)}
        y1={PAD.top}
        y2={PAD.top + plotH}
        stroke="#ea580c"
        strokeWidth={1.25}
      />

      {/* Cohort MEAN per-well EUR — context only; dashed so the
          median above reads as the primary reference. */}
      <line
        x1={xScale(wellMean)}
        x2={xScale(wellMean)}
        y1={PAD.top}
        y2={PAD.top + plotH}
        stroke="#f59e0b"
        strokeWidth={1.0}
        strokeDasharray="6 3"
      />

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

      {dots.map((d) => (
        <circle
          key={d.key}
          cx={d.x}
          cy={d.y}
          r={4}
          fill="#16a34a"
          stroke="#064e3b"
          strokeWidth={0.5}
        />
      ))}

      <text
        x={PAD.left + plotW / 2}
        y={height - 4}
        textAnchor="middle"
        fontSize="13"
        fill="#374151"
      >
        {`${stream.charAt(0).toUpperCase()}${stream.slice(1)} EUR / ft (${unitLabel})`}
      </text>

      <g transform={`translate(${PAD.left + 8}, ${PAD.top + 8})`}>
        <rect width="168" height={prevVal != null ? 124 : 104} fill="#ffffff" stroke="#e5e7eb" />
        <circle cx={10} cy={14} r={4} fill="#16a34a" />
        <text x={20} y={17} fontSize="12" fill="#374151">
          Wells (n={n})
        </text>
        <line x1={4} y1={33} x2={16} y2={33} stroke="#6b7280" strokeWidth={1} />
        <text x={20} y={36} fontSize="12" fill="#374151">
          Probit Fit
        </text>
        <line
          x1={4}
          y1={52}
          x2={16}
          y2={52}
          stroke="#ea580c"
          strokeWidth={1.25}
        />
        <text x={20} y={55} fontSize="12" fill="#374151">
          Wells Median ({wellMedian.toFixed(1)})
        </text>
        <line
          x1={4}
          y1={71}
          x2={16}
          y2={71}
          stroke="#f59e0b"
          strokeWidth={1.0}
          strokeDasharray="3 2"
        />
        <text x={20} y={74} fontSize="12" fill="#374151">
          Wells Avg ({wellMean.toFixed(1)})
        </text>
        <line x1={4} y1={90} x2={16} y2={90} stroke="#0f172a" strokeWidth={2} />
        <text x={20} y={93} fontSize="12" fill="#374151">
          {tcVal != null ? `Type Curve (${tcVal.toFixed(1)})` : "Type Curve (—)"}
        </text>
        {prevVal != null && (
          <>
            <line
              x1={4}
              y1={109}
              x2={16}
              y2={109}
              stroke="#6b7280"
              strokeWidth={1.5}
              strokeDasharray="2 3"
            />
            <text x={20} y={112} fontSize="12" fill="#374151">
              {prevLabel ? `Prev: ${prevLabel}` : "Previous TC"}
            </text>
          </>
        )}
      </g>
    </svg>
  );
}

function pickLogTicks(min: number, max: number): number[] {
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
