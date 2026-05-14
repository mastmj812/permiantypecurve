// Forecast grid: one row per well, columns for fit params + EUR + R² per
// stream. Click any cell in a row to open the detail modal for that well.

import { useMemo } from "react";

import type { ForecastRow, Stream, WellCurvesResponse } from "../api/forecasts";
import { Sparkline } from "./Sparkline";
import type { SeriesPoint } from "./DeclineChart";

interface Props {
  forecasts: ForecastRow[];
  curvesByApi14: Record<string, WellCurvesResponse | undefined>;
  onRowClick: (api14: string) => void;
}

type ByApi = Record<string, Record<Stream, ForecastRow | undefined>>;

export function ForecastGrid({ forecasts, curvesByApi14, onRowClick }: Props) {
  const byApi: ByApi = useMemo(() => {
    const out: ByApi = {};
    for (const f of forecasts) {
      out[f.api14] ??= { oil: undefined, gas: undefined, water: undefined };
      out[f.api14]![f.stream] = f;
    }
    return out;
  }, [forecasts]);

  const api14s = Object.keys(byApi).sort();

  return (
    <div className="forecast-grid">
      <table>
        <thead>
          <tr>
            <th>api14</th>
            <th>oil qi</th>
            <th title="Arps nominal Di (per year)">Di (nom)</th>
            <th title="Effective first-year decline (% rate drop in year 1)">
              Di (1yr eff)
            </th>
            <th>b</th>
            <th>EUR (BBL)</th>
            <th>R²</th>
            <th>gas EUR (MCF)</th>
            <th>water EUR (BBL)</th>
            <th>oil sparkline</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {api14s.map((api14) => {
            const row = byApi[api14]!;
            const oil = row.oil;
            const gas = row.gas;
            const water = row.water;
            const cur = curvesByApi14[api14];
            const oilCurves = cur?.streams.find((s) => s.stream === "oil");
            const history: SeriesPoint[] = oilCurves
              ? oilCurves.history_rate
                  .map((y, i) => (y == null ? null : { t: i, y }))
                  .filter((p): p is SeriesPoint => p !== null)
              : [];
            const forecast: SeriesPoint[] = oilCurves
              ? oilCurves.forecast_rate.map((y, i) => ({ t: i, y }))
              : [];
            return (
              <tr
                key={api14}
                onClick={() => onRowClick(api14)}
                className={oil?.manual_override ? "row-overridden" : ""}
              >
                <td>{api14}</td>
                <td>{fmt(oil?.qi)}</td>
                <td>{fmt(oil?.di_initial, 3)}</td>
                <td>
                  {oil?.di_effective != null
                    ? `${(oil.di_effective * 100).toFixed(1)}%`
                    : "—"}
                </td>
                <td>{fmt(oil?.b, 2)}</td>
                <td>{fmtVol(oil?.eur)}</td>
                <td>{fmt(oil?.fit_r2, 3)}</td>
                <td>{fmtVol(gas?.eur)}</td>
                <td>{fmtVol(water?.eur)}</td>
                <td>
                  <Sparkline history={history} forecast={forecast} />
                </td>
                <td>
                  {oil?.locked && <span className="badge">locked</span>}
                  {oil?.manual_override && <span className="badge badge-warn">edited</span>}
                  {oil && oil.fit_r2 != null && oil.fit_r2 < 0.7 && (
                    <span className="badge badge-err">low R²</span>
                  )}
                  {oil?.fit_at_bound && (
                    <span
                      className="badge badge-warn"
                      title={oil.bound_note ?? "fit pinned at a bound"}
                    >
                      at bound
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
          {api14s.length === 0 && (
            <tr>
              <td colSpan={11} className="muted" style={{ textAlign: "center" }}>
                no forecasts yet — select wells on the map and click "Forecast" to start
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function fmt(v: number | null | undefined, digits = 1): string {
  if (v == null) return "—";
  return v.toFixed(digits);
}
function fmtVol(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return v.toFixed(0);
}
