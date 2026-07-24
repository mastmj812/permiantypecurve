// Geologic risking — display-time application of a curve's per-stream
// scalar multipliers. TS mirror of backend app/type_curves/risking.py.
//
// The API always serves the CLEAN series (a risked series would
// double-risk through the TweakPanel round-trip: edit a displayed
// risked qi -> save -> re-risk on the next read). Display code risks
// the derived view; file exports risk server-side at their own seams.
//
// Arps is homogeneous in qi: scaling qi/qo scales the whole rate
// vector and every EUR by the same factor. Shape params (b, Di, Df,
// peak_index) and r2 pass through — risking changes magnitude, not
// decline character — and a uniform positive scalar preserves the SPE
// percentile order (P10 >= P50 >= P90).

import type { FittedTypeCurve, RiskMultipliers, StreamSeries } from "../api/typeCurves";

export type Stream = "oil" | "gas" | "water";

export const RISK_STREAMS: readonly Stream[] = ["oil", "gas", "water"];

/** {oil, gas, water} all present, absent keys resolved to 1.0. */
export function normalizeMultipliers(
  raw: RiskMultipliers | null | undefined,
): Record<Stream, number> {
  const out: Record<Stream, number> = { oil: 1.0, gas: 1.0, water: 1.0 };
  for (const s of RISK_STREAMS) {
    const v = raw?.[s];
    if (typeof v === "number" && Number.isFinite(v) && v > 0) out[s] = v;
  }
  return out;
}

/** True when any stream's multiplier differs from 1.0. */
export function isRisked(raw: RiskMultipliers | null | undefined): boolean {
  const m = normalizeMultipliers(raw);
  return RISK_STREAMS.some((s) => m[s] !== 1.0);
}

/** "×0.85 oil · ×0.90 gas" — badge text for the non-1.0 streams. */
export function riskingLabel(raw: RiskMultipliers | null | undefined): string {
  const m = normalizeMultipliers(raw);
  return RISK_STREAMS.filter((s) => m[s] !== 1.0)
    .map((s) => `×${m[s].toFixed(2)} ${s}`)
    .join(" · ");
}

export function scaleRates(
  values: Array<number | null> | null | undefined,
  mul: number,
): Array<number | null> | null {
  if (!values) return null;
  if (mul === 1.0) return values;
  return values.map((v) => (v == null ? v : v * mul));
}

function scaleFit(fit: FittedTypeCurve, mul: number): FittedTypeCurve {
  return {
    ...fit,
    qi: fit.qi * mul,
    eur_per_unit: fit.eur_per_unit * mul,
    smoothed_rate: fit.smoothed_rate?.map((v) => v * mul) ?? fit.smoothed_rate,
    ...(fit.qo != null ? { qo: fit.qo * mul } : {}),
    ...(fit.ramp_eur != null ? { ramp_eur: fit.ramp_eur * mul } : {}),
    ...(fit.arps_eur != null ? { arps_eur: fit.arps_eur * mul } : {}),
  };
}

/**
 * Risked view of one stream's series for display. Identity (same
 * object) at mul 1.0. Scales the percentile/mean rate arrays, implied
 * EURs, the published fit, every per-percentile fit, and the
 * per-percentile EUR map; well_count passes through.
 */
export function riskStreamSeries(s: StreamSeries, mul: number): StreamSeries {
  if (mul === 1.0) return s;
  const scaleMap = <V extends number | null>(
    m: Record<string, V> | null | undefined,
  ): Record<string, V> | null =>
    m
      ? (Object.fromEntries(
          Object.entries(m).map(([k, v]) => [k, v == null ? v : v * mul]),
        ) as Record<string, V>)
      : null;
  return {
    ...s,
    p10: scaleRates(s.p10, mul) ?? s.p10,
    p25: scaleRates(s.p25, mul) ?? s.p25,
    p50: scaleRates(s.p50, mul) ?? s.p50,
    p75: scaleRates(s.p75, mul) ?? s.p75,
    p90: scaleRates(s.p90, mul) ?? s.p90,
    mean: scaleRates(s.mean, mul) ?? s.mean,
    implied_eur_per_1000ft: scaleMap(s.implied_eur_per_1000ft) ?? {},
    fitted: s.fitted ? scaleFit(s.fitted, mul) : s.fitted,
    fitted_eur_per_unit: scaleMap(s.fitted_eur_per_unit),
    fitted_per_percentile: s.fitted_per_percentile
      ? Object.fromEntries(
          Object.entries(s.fitted_per_percentile).map(([k, f]) => [
            k,
            f ? scaleFit(f, mul) : f,
          ]),
        )
      : s.fitted_per_percentile,
  };
}
