// Comparison table for the type-curve slide export.
//
// Two rows: the current curve and an optional Previous TC (the user's
// "Compare with" pick on TypeCurvePage). 16 columns total — six per
// stream for oil/gas, four for water. Mirrors the column groups in the
// reference slide screenshot, but treats gas as a parallel Arps stream
// to oil rather than breaking GOR out as derived columns (no piecewise-
// GOR model exists upstream).
//
// All values read from `curve.series.streams[stream].fitted`. Missing
// fits (older saves, all-zero series) render as em-dashes.

import { effectiveDecline } from "../../api/forecasts";
import type { TypeCurveRow } from "../../api/typeCurves";
import { normalizeMultipliers, riskedNameSuffix } from "../../type_curves/risking";

interface Props {
  current: TypeCurveRow;
  previous: TypeCurveRow | null;
}

// Saved curves are normalized per-1,000 lateral ft (the `per_lateral_ft`
// basis stores rates/EUR on that unit). The slide charts scale these
// by 10 for the per-10,000-ft display the engineers read off of; the
// table follows the same convention so the numbers visually match
// what the charts depict. Applies to rates (qi/qo) and EUR — Mo, B,
// Di are unit-independent and pass through unscaled.
const PER_10K_FACTOR = 10;

type Stream = "oil" | "gas" | "water";

// Geologic risking applies to the rate/EUR-dimensioned params this
// table renders (qi/qo/eur_per_unit); b/Di/Mo are shape params and pass
// through. MUST stay byte-for-byte with the backend's
// app/exports/param_row.py (the PPTX cell strings) — change both.
function getFitted(curve: TypeCurveRow, stream: Stream) {
  const streams = (curve.series as { streams?: Record<string, { fitted?: Record<string, unknown> | null }> }).streams;
  const fitted = streams?.[stream]?.fitted;
  if (!fitted) return null;
  const mul = normalizeMultipliers(curve.risk_multipliers)[stream];
  if (mul === 1.0) return fitted;
  const risked: Record<string, unknown> = { ...fitted };
  for (const key of ["qi", "qo", "eur_per_unit"]) {
    const n = num(fitted[key]);
    if (n != null) risked[key] = n * mul;
  }
  return risked;
}

function num(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function scaleNum(v: unknown, factor: number): number | null {
  const n = num(v);
  return n == null ? null : n * factor;
}

const intFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const eurFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function fmtRate(v: unknown): string {
  const n = num(v);
  return n == null ? "—" : intFmt.format(Math.round(n));
}

function fmtMo(v: unknown): string {
  const n = num(v);
  return n == null ? "—" : n.toFixed(1);
}

function fmtB(v: unknown): string {
  const n = num(v);
  return n == null ? "—" : n.toFixed(2);
}

// Display Di as the 1-yr effective decline rather than the Arps nominal
// param. Operators read decline in effective terms ("74% first-year
// decline"), and that's what the slide deck downstream expects. Math
// matches the chart's tooltip / TweakPanel convention via the shared
// `effectiveDecline` helper.
function fmtDiEffective(Di: unknown, b: unknown): string {
  const dn = num(Di);
  const bn = num(b);
  if (dn == null || dn <= 0) return "—";
  const eff = effectiveDecline(dn, bn);
  return eff > 0 ? eff.toFixed(2) : "—";
}

function fmtEur(v: unknown): string {
  const n = num(v);
  return n == null ? "—" : eurFmt.format(Math.round(n));
}

const HEADERS: string[] = [
  "Type Curve Name",
  "Oil Initial", "Oil Mo", "Oil Peak", "Oil B", "Oil Di", "Oil EUR",
  "Gas Initial", "Gas Mo", "Gas Peak", "Gas B", "Gas Di", "Gas EUR",
  "Water Peak", "Water B", "Water Di", "Water EUR",
];

function rowCells(curve: TypeCurveRow): string[] {
  const oil = getFitted(curve, "oil") ?? {};
  const gas = getFitted(curve, "gas") ?? {};
  const wat = getFitted(curve, "water") ?? {};
  const F = PER_10K_FACTOR;
  // [RISKED ×0.80] suffix mirrors app/exports/param_row.py exactly.
  const name = `${curve.name}${riskedNameSuffix(curve.risk_multipliers)}`;
  return [
    name,
    fmtRate(scaleNum(oil["qo"], F)),
    fmtMo(oil["peak_index"]),
    fmtRate(scaleNum(oil["qi"], F)),
    fmtB(oil["b"]),
    fmtDiEffective(oil["Di"], oil["b"]),
    fmtEur(scaleNum(oil["eur_per_unit"], F)),
    fmtRate(scaleNum(gas["qo"], F)),
    fmtMo(gas["peak_index"]),
    fmtRate(scaleNum(gas["qi"], F)),
    fmtB(gas["b"]),
    fmtDiEffective(gas["Di"], gas["b"]),
    fmtEur(scaleNum(gas["eur_per_unit"], F)),
    fmtRate(scaleNum(wat["qi"], F)),
    fmtB(wat["b"]),
    fmtDiEffective(wat["Di"], wat["b"]),
    fmtEur(scaleNum(wat["eur_per_unit"], F)),
  ];
}

export function SlideParamTable({ current, previous }: Props) {
  const rows: string[][] = [rowCells(current)];
  if (previous) rows.push(rowCells(previous));

  return (
    <table className="slide-param-table">
      <thead>
        <tr>
          {HEADERS.map((h) => (
            <th key={h}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((cells, ri) => (
          <tr key={ri}>
            {cells.map((c, ci) => (
              <td key={ci} className={ci === 0 ? "slide-param-name" : "slide-param-num"}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
