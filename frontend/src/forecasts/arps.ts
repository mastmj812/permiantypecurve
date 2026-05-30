// Pure Arps math, mirrored from the backend's `app.forecasting.models` +
// `app.type_curves.fit_p50`. Shared between the Review and Type Curve
// pages so both tabs agree on what "EUR" means: the raw 50-yr integral
// of the persisted fit, no economic-limit cutoff. This tool is strictly
// for technical type-curve / decline generation — economics is done
// downstream on the exported workbook.

export const DAYS_PER_MONTH = 30.4375;
export const FULL_FORECAST_N_MONTHS = 600;

export interface RampArpsParams {
  qi: number;
  Di: number;
  b: number;
  Df: number;
  // Optional ramp prefix — used by type-curve fits where the aggregated
  // P50 includes the ramp-up months. Per-well forecasts start at peak
  // (peak_index = 0, qo = qi) and don't supply these.
  qo?: number;
  peak_index?: number;
}

// Modified-hyperbolic decline: hyperbolic phase until the instantaneous
// nominal decline D(t) drops to the terminal Df, then exponential.
// Continuous at the switchover point.
export function evalModifiedHyperbolicRate(
  tYears: number,
  qi: number,
  Di: number,
  b: number,
  Df: number,
): number {
  if (tYears <= 0) return qi;
  if (Df <= 0 || Di <= 0 || Df >= Di || Math.abs(b) < 1e-6) {
    const bSafe = Math.max(b, 1e-6);
    return qi / Math.pow(1 + bSafe * Di * tYears, 1 / bSafe);
  }
  const tSwitch = (Di / Df - 1) / (b * Di);
  if (tYears <= tSwitch) {
    return qi / Math.pow(1 + b * Di * tYears, 1 / b);
  }
  const qSwitch = qi * Math.pow(Df / Di, 1 / b);
  return qSwitch * Math.exp(-Df * (tYears - tSwitch));
}

// Linear ramp from qo at month 0 up to qi at peak_index, then modified-
// hyperbolic from peak forward. Returns a number[] of length nMonths.
export function buildRampArpsRate(
  fit: RampArpsParams,
  nMonths: number,
): number[] {
  const out: number[] = [];
  const peakIndex = fit.peak_index ?? 0;
  const qo = fit.qo ?? fit.qi;
  if (peakIndex > 0) {
    const ramp = Math.min(peakIndex + 1, nMonths);
    for (let i = 0; i < ramp; i++) {
      out.push(qo + (fit.qi - qo) * (i / peakIndex));
    }
  } else {
    out.push(fit.qi);
  }
  const remaining = nMonths - out.length;
  for (let k = 1; k <= remaining; k++) {
    out.push(evalModifiedHyperbolicRate(k / 12, fit.qi, fit.Di, fit.b, fit.Df));
  }
  return out.slice(0, nMonths);
}

// 50-yr raw integral of an Arps fit. Trapezoid rule on monthly rate
// samples (last month flat-extrapolated). No economic-limit cutoff —
// see module header.
//
// arr[i] = q(t = i months). Integral ≈ Σ (arr[i] + arr[i+1])/2 * dt.
// Closes the small (~0.2% on Permian fits) systematic bias the old
// right-endpoint rectangle rule had vs. the closed-form scipy.quad
// (which is what the backend stores as `eur_per_unit` and what the
// PPTX deck reads).
export function eurFromArpsParams(
  fit: RampArpsParams | null | undefined,
): number | null {
  if (!fit) return null;
  if (
    !Number.isFinite(fit.qi) ||
    !Number.isFinite(fit.Di) ||
    !Number.isFinite(fit.b) ||
    !Number.isFinite(fit.Df)
  ) {
    return null;
  }
  const rates = buildRampArpsRate(fit, FULL_FORECAST_N_MONTHS);
  let cum = 0;
  for (let i = 0; i < rates.length; i++) {
    const a = rates[i];
    if (a === undefined || !Number.isFinite(a)) continue;
    const next = rates[i + 1];
    const b = next !== undefined && Number.isFinite(next) ? next : a;
    cum += ((a + b) / 2) * DAYS_PER_MONTH;
  }
  return cum;
}

// Convenience for per-well decline rows: pulls qi/Di/b/Df plus the
// optional ramp prefix (qo + peak_index_months) out of the
// `Forecast.params` dict and integrates over 50 years (no econ
// cutoff). The ramp prefix used to be ignored here, which made the
// client-side EUR drop by ramp_eur vs. the stored total — visible
// as "saved EUR < preview EUR" until preview replaced the displayed
// value. Returns null when any of the four core Arps params are
// missing/non-finite — caller should fall back to the stored `eur`
// in that case.
export function eurFromForecastParams(
  params: Record<string, number> | null | undefined,
): number | null {
  if (!params) return null;
  const qi = params.qi;
  const Di = params.Di;
  const b = params.b;
  const Df = params.Df;
  if (
    qi === undefined || !Number.isFinite(qi) ||
    Di === undefined || !Number.isFinite(Di) ||
    b === undefined || !Number.isFinite(b) ||
    Df === undefined || !Number.isFinite(Df)
  ) {
    return null;
  }
  const qo = params.qo;
  const peakIndex = params.peak_index_months;
  return eurFromArpsParams({
    qi,
    Di,
    b,
    Df,
    qo: qo !== undefined && Number.isFinite(qo) ? qo : undefined,
    peak_index:
      peakIndex !== undefined && Number.isFinite(peakIndex) ? peakIndex : undefined,
  });
}
