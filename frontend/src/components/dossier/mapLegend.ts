// Map legends burned INTO the panel snapshot. The PPTX capture reads
// only the map's snapshot <img>, so an HTML legend beside the map never
// reaches the deck — the dossier's maps compose their legend onto the
// canvas copy instead (bottom-left box: swatch rows, optional colour bar).

import type { ExpressionSpecification } from "maplibre-gl";

// viridis, 9 stops — low EUR/ft dark purple -> high yellow. Shared by
// the MapLibre interpolate and the snapshot colour bar so both agree.
export const VIRIDIS: readonly string[] = [
  "#440154", "#472d7b", "#3b528b", "#2c728e", "#21918c",
  "#28ae80", "#5ec962", "#addc30", "#fde725",
];
export const NO_FIT_COLOR = "#9ca3af";

export interface LegendRow {
  color: string;
  label: string;
  kind: "line" | "dash" | "thin";
}

export interface ColorBar {
  lo: number;
  hi: number;
  label: string; // e.g. "anduin oil EUR, bbl/ft"
}

export interface LegendSpec {
  rows: LegendRow[];
  colorBar?: ColorBar;
}

// MapLibre colour expression for a numeric property over [lo, hi];
// a missing/null value paints NO_FIT_COLOR (MVT/GeoJSON nulls are
// handled with ["has"], never == null).
export function viridisExpression(prop: string, lo: number, hi: number): ExpressionSpecification {
  const span = hi > lo ? hi - lo : 1;
  const stops: unknown[] = [];
  VIRIDIS.forEach((c, i) => {
    stops.push(lo + (span * i) / (VIRIDIS.length - 1), c);
  });
  return [
    "case",
    ["has", prop],
    ["interpolate", ["linear"], ["get", prop], ...stops],
    NO_FIT_COLOR,
  ] as ExpressionSpecification;
}

export function viridisColor(v: number | null, lo: number, hi: number): string {
  if (v === null) return NO_FIT_COLOR;
  const t = Math.min(1, Math.max(0, hi > lo ? (v - lo) / (hi - lo) : 0.5));
  return VIRIDIS[Math.round(t * (VIRIDIS.length - 1))]!;
}

// Copy the map canvas and draw the legend box on top; returns a PNG
// data URL. Sizes scale with the canvas/CSS ratio (devicePixelRatio).
export function composeSnapshot(
  canvas: HTMLCanvasElement,
  cssWidth: number,
  legend: LegendSpec | null,
): string {
  if (!legend) return canvas.toDataURL("image/png");
  const out = document.createElement("canvas");
  out.width = canvas.width;
  out.height = canvas.height;
  const ctx = out.getContext("2d");
  if (!ctx) return canvas.toDataURL("image/png");
  ctx.drawImage(canvas, 0, 0);
  const k = canvas.width / cssWidth;
  const pad = 6 * k;
  const rowH = 15 * k;
  const font = `${11 * k}px sans-serif`;
  ctx.font = font;
  const textW = Math.max(
    ...legend.rows.map((r) => ctx.measureText(r.label).width),
    legend.colorBar ? ctx.measureText(legend.colorBar.label).width : 0,
    120 * k,
  );
  const barH = legend.colorBar ? 46 * k : 0;
  const boxW = pad * 3 + 22 * k + textW;
  const boxH = pad * 2 + legend.rows.length * rowH + barH;
  const x0 = 8 * k;
  const y0 = canvas.height - boxH - 8 * k;
  ctx.fillStyle = "rgba(255,255,255,0.9)";
  ctx.strokeStyle = "#9ca3af";
  ctx.lineWidth = 1 * k;
  ctx.fillRect(x0, y0, boxW, boxH);
  ctx.strokeRect(x0, y0, boxW, boxH);
  ctx.textBaseline = "middle";
  legend.rows.forEach((r, i) => {
    const y = y0 + pad + rowH * i + rowH / 2;
    ctx.strokeStyle = r.color;
    ctx.lineWidth = (r.kind === "thin" ? 1.2 : 3.5) * k;
    ctx.setLineDash(r.kind === "dash" ? [5 * k, 3 * k] : []);
    ctx.beginPath();
    ctx.moveTo(x0 + pad, y);
    ctx.lineTo(x0 + pad + 22 * k, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#111827";
    ctx.fillText(r.label, x0 + pad * 2 + 22 * k, y);
  });
  if (legend.colorBar) {
    // label line, then the bar, then the end values under its ends
    const cb = legend.colorBar;
    const top = y0 + pad + legend.rows.length * rowH;
    const w = boxW - pad * 2;
    ctx.fillStyle = "#111827";
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    ctx.fillText(cb.label, x0 + pad, top + 7 * k);
    const y = top + 15 * k;
    const g = ctx.createLinearGradient(x0 + pad, 0, x0 + pad + w, 0);
    VIRIDIS.forEach((c, i) => g.addColorStop(i / (VIRIDIS.length - 1), c));
    ctx.fillStyle = g;
    ctx.fillRect(x0 + pad, y, w, 9 * k);
    ctx.fillStyle = "#111827";
    ctx.textBaseline = "top";
    ctx.fillText(`≤${cb.lo.toFixed(0)}`, x0 + pad, y + 11 * k);
    ctx.textAlign = "right";
    ctx.fillText(`≥${cb.hi.toFixed(0)}`, x0 + pad + w, y + 11 * k);
    ctx.textAlign = "left";
  }
  return out.toDataURL("image/png");
}
