// Per-well detail modal: Cartesian + semi-log plots, editable params with
// live re-render against /api/forecasts/preview, manual-override lock,
// stream switcher (oil / gas / water), prodday-rate toggle for diagnostic.

import { useEffect, useMemo, useState } from "react";

import {
  type ForecastRow,
  type ModelType,
  type Stream,
  type StreamCurves,
  effectiveDecline,
  fetchWellCurves,
  patchForecast,
  previewForecast,
} from "../api/forecasts";
import { DeclineChart, type SeriesPoint } from "./DeclineChart";

interface Props {
  api14: string;
  forecasts: ForecastRow[];
  onClose: () => void;
  onSaved: (updated: ForecastRow) => void;
  // Nav through the parent's current sort/filter ordering. Undefined
  // when at the boundary so the arrow disables. position is shown as
  // "N of M" so the reviewer knows where they are in the queue.
  onPrev?: () => void;
  onNext?: () => void;
  position?: { index: number; total: number };
}

const STREAM_UNITS: Record<Stream, { rate: string; cum: string }> = {
  oil: { rate: "BOPD", cum: "BBL" },
  gas: { rate: "MCFD", cum: "MCF" },
  water: { rate: "BWPD", cum: "BBL" },
};

export function ForecastDetailModal({
  api14,
  forecasts,
  onClose,
  onSaved,
  onPrev,
  onNext,
  position,
}: Props) {
  const [stream, setStream] = useState<Stream>("oil");
  const [showProdday, setShowProdday] = useState(false);
  const [curves, setCurves] = useState<StreamCurves | null>(null);
  const [loading, setLoading] = useState(true);

  const forecastForStream = useMemo(
    () => forecasts.find((f) => f.stream === stream) ?? null,
    [forecasts, stream],
  );

  // Editable parameter state — initialized from the fitted forecast.
  const [editQi, setEditQi] = useState<number | null>(null);
  const [editDi, setEditDi] = useState<number | null>(null);
  const [editB, setEditB] = useState<number | null>(null);
  const [editDf, setEditDf] = useState<number | null>(null);
  const [previewPoints, setPreviewPoints] = useState<SeriesPoint[]>([]);
  const [previewCumPoints, setPreviewCumPoints] = useState<SeriesPoint[]>([]);
  const [previewEur, setPreviewEur] = useState<number | null>(null);

  useEffect(() => {
    if (!forecastForStream) return;
    setEditQi(forecastForStream.params.qi ?? null);
    setEditDi(forecastForStream.params.Di ?? null);
    setEditB(forecastForStream.params.b ?? null);
    setEditDf(forecastForStream.params.Df ?? 0.08);
    setPreviewPoints([]);
    setPreviewCumPoints([]);
    setPreviewEur(null);
  }, [forecastForStream?.id]);

  // Keyboard nav while the modal is open. Skip when focus is in an
  // input/textarea so editing fit parameters doesn't trigger nav.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) {
        return;
      }
      if (e.key === "ArrowLeft" && onPrev) {
        e.preventDefault();
        onPrev();
      } else if (e.key === "ArrowRight" && onNext) {
        e.preventDefault();
        onNext();
      } else if (e.key === "Escape") {
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onPrev, onNext, onClose]);

  useEffect(() => {
    setLoading(true);
    fetchWellCurves(api14)
      .then((r) => {
        const found = r.streams.find((s) => s.stream === stream) ?? null;
        setCurves(found);
      })
      .finally(() => setLoading(false));
  }, [api14, stream]);

  const history = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    const ratesSrc = showProdday ? curves.history_prodday_rate : curves.history_rate;
    return curves.history_rate
      .map((_, i) => {
        const v = ratesSrc[i];
        return v == null ? null : { t: i, y: v };
      })
      .filter((p): p is SeriesPoint => p !== null);
  }, [curves, showProdday]);

  const fitForecast = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    // Forecast t-axis: months past peak. Index 0 in the forecast arrays
    // corresponds to peak_month + 1 month, etc.
    return curves.forecast_rate.map((y, i) => ({ t: i, y }));
  }, [curves]);

  // Cumulative-vs-time series. History plots at t = i (months from first
  // production). The /curves endpoint emits forecast_cum starting at 0
  // (peak-month boundary, by backend convention), so we shift the
  // forecast cum line to pick up from the history line:
  //   x: peak-month index in the history timeline + forecast i
  //   y: history_cum at the peak month + forecast_cum[i]
  // Without this shift the forecast cum line restarts at (0, 0), which
  // visually reads as the cum-actual line "jumping" off the cum-forecast
  // line whenever the live-preview overlay clears (e.g. after Save Override).
  const cumChartOffset = useMemo(() => {
    if (!curves) return { xOffset: 0, yOffset: 0 };
    const peakMonth = curves.forecast_months[0];
    const idx = peakMonth ? curves.months.indexOf(peakMonth) : -1;
    // Peak inside the history window → place forecast at that index.
    // Peak past the last reported month (rare; would mean the fit's peak
    // is in the future) → drop the forecast at the end of history so the
    // line still continues without a y-jump.
    const xOffset = idx >= 0 ? idx : Math.max(0, curves.history_cum.length - 1);
    const yOffset =
      curves.history_cum[Math.min(xOffset, curves.history_cum.length - 1)] ?? 0;
    return { xOffset, yOffset };
  }, [curves]);

  const historyCum = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    return curves.history_cum
      .map((v, i) => (v == null ? null : { t: i, y: v }))
      .filter((p): p is SeriesPoint => p !== null);
  }, [curves]);

  const forecastCum = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    const { xOffset, yOffset } = cumChartOffset;
    return curves.forecast_cum.map((y, i) => ({
      t: xOffset + i,
      y: y + yOffset,
    }));
  }, [curves, cumChartOffset]);

  const displayedForecast = previewPoints.length > 0 ? previewPoints : fitForecast;
  const displayedForecastCum =
    previewCumPoints.length > 0 ? previewCumPoints : forecastCum;

  // ----------- live re-render on edits -----------
  async function recompute() {
    if (!forecastForStream) return;
    const params: Record<string, number> = {};
    if (editQi != null) params.qi = editQi;
    if (editDi != null) params.Di = editDi;
    if (editB != null) params.b = editB;
    if (editDf != null) params.Df = editDf;
    try {
      const p = await previewForecast({
        model_type: forecastForStream.model_type,
        params,
        economic_limit:
          stream === "oil" ? 5 : stream === "gas" ? 30 : 50,
      });
      const points = p.rate.map((y, i) => ({ t: p.t_years[i]! * 12, y }));
      // Apply the same peak-anchored offset the saved cum line uses so
      // the live preview stays continuous with history_cum. Without this
      // the preview line would sit at (0,0)-and-up while the saved line
      // (post-fix) sits at (peakOffset, cumAtPeak)-and-up, and the
      // user would see the cum-forecast line "jump" the instant they
      // hit Save Override.
      const { xOffset, yOffset } = cumChartOffset;
      const cumPoints = p.cum.map((y, i) => ({
        t: xOffset + (p.t_years[i]! * 12),
        y: y + yOffset,
      }));
      setPreviewPoints(points);
      setPreviewCumPoints(cumPoints);
      setPreviewEur(p.eur);
    } catch (e) {
      console.error("preview failed", e);
    }
  }

  async function save() {
    if (!forecastForStream) return;
    const params: Record<string, number> = { ...forecastForStream.params };
    if (editQi != null) params.qi = editQi;
    if (editDi != null) params.Di = editDi;
    if (editB != null) params.b = editB;
    if (editDf != null) params.Df = editDf;
    const updated = await patchForecast(forecastForStream.id, {
      params,
      manual_override: true,
    });
    onSaved(updated);
    setPreviewPoints([]);
    setPreviewCumPoints([]);
    setPreviewEur(null);
    // Refetch curves so fitForecast reflects the saved override. Without
    // this, clearing the preview overlay snaps the chart back to the
    // original auto-fit curve (cached in `curves` from the modal-open
    // fetch), making it look like the save reverted.
    try {
      const r = await fetchWellCurves(api14);
      const found = r.streams.find((s) => s.stream === stream) ?? null;
      setCurves(found);
    } catch (e) {
      console.error("refetch curves after save failed", e);
    }
  }

  async function toggleLock() {
    if (!forecastForStream) return;
    const updated = await patchForecast(forecastForStream.id, {
      locked: !forecastForStream.locked,
    });
    onSaved(updated);
  }

  const units = STREAM_UNITS[stream];

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button
              type="button"
              className="tb-btn"
              onClick={onPrev}
              disabled={!onPrev}
              title="Previous well (←)"
              aria-label="Previous well"
            >
              ◄
            </button>
            <button
              type="button"
              className="tb-btn"
              onClick={onNext}
              disabled={!onNext}
              title="Next well (→)"
              aria-label="Next well"
            >
              ►
            </button>
            {position && (
              <span className="muted" style={{ fontSize: 11 }}>
                {position.index} of {position.total}
              </span>
            )}
            <strong style={{ marginLeft: 8 }}>{api14}</strong>
            {forecasts[0]?.well_name && (
              <span>— {forecasts[0].well_name}</span>
            )}
            <span className="muted">
              {forecastForStream?.model_type ?? "no forecast"}
            </span>
          </div>
          <button type="button" className="link-btn" onClick={onClose}>
            close
          </button>
        </header>

        <div className="modal-body">
          <div className="modal-toolbar">
            <div className="toolbar-group">
              <span className="toolbar-label">Stream:</span>
              {(["oil", "gas", "water"] as Stream[]).map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`tb-btn ${stream === s ? "tb-active" : ""}`}
                  onClick={() => setStream(s)}
                >
                  {s}
                </button>
              ))}
            </div>
            <label className="chk-inline">
              <input
                type="checkbox"
                checked={showProdday}
                onChange={(e) => setShowProdday(e.target.checked)}
              />
              show prodday rate (diagnostic)
            </label>
          </div>

          {loading && <p className="muted">loading curves…</p>}

          <div className="chart-row">
            <DeclineChart
              history={history}
              forecast={displayedForecast}
              yAxisType="linear"
              yLabel={units.rate}
            />
            <DeclineChart
              history={history}
              forecast={displayedForecast}
              yAxisType="log"
              yLabel={units.rate}
            />
          </div>

          {/* Cum-vs-time row. Linear-Y only — log on a monotone cum
              curve doesn't help. The cum-forecast line picks up from
              where cum-actual leaves off via cumChartOffset (peak-month
              index + cum-at-peak); see forecastCum useMemo above. */}
          <div className="chart-row">
            <DeclineChart
              history={historyCum}
              forecast={displayedForecastCum}
              yAxisType="linear"
              yLabel={units.cum}
              xLabel="Months from first production"
            />
          </div>

          {forecastForStream && (
            <div className="param-editor">
              <h3>Fit parameters</h3>
              <table>
                <tbody>
                  <ParamRow
                    label="qi"
                    unit={units.rate}
                    value={editQi}
                    onChange={setEditQi}
                  />
                  <ParamRow
                    label="Di (nominal)"
                    unit="/yr"
                    value={editDi}
                    onChange={setEditDi}
                  />
                  {editDi != null && (
                    <tr>
                      <th>Di (1yr effective)</th>
                      <td colSpan={2}>
                        <span className="muted">
                          {(effectiveDecline(editDi, editB) * 100).toFixed(1)}%
                        </span>
                      </td>
                    </tr>
                  )}
                  {editB != null && (
                    <ParamRow label="b" unit="" value={editB} onChange={setEditB} />
                  )}
                  {editDf != null && (
                    <ParamRow
                      label="Df (terminal)"
                      unit="/yr"
                      value={editDf}
                      onChange={setEditDf}
                    />
                  )}
                </tbody>
              </table>
              <div className="param-actions">
                <button type="button" onClick={recompute}>
                  preview
                </button>
                <button type="button" className="btn-primary" onClick={save}>
                  save override
                </button>
                <button
                  type="button"
                  className={forecastForStream.locked ? "tb-active tb-btn" : "tb-btn"}
                  onClick={toggleLock}
                  title={
                    forecastForStream.locked
                      ? "Locked — bulk re-fit will skip this stream"
                      : "Unlocked — bulk re-fit will overwrite"
                  }
                >
                  {forecastForStream.locked ? "locked" : "lock"}
                </button>
              </div>
              <div className="param-stats">
                <Stat label="EUR" value={fmtEur(previewEur ?? forecastForStream.eur, units.cum)} />
                <Stat
                  label="Fit R²"
                  value={
                    forecastForStream.fit_r2 != null
                      ? forecastForStream.fit_r2.toFixed(3)
                      : "—"
                  }
                />
                <Stat
                  label="Peak"
                  value={
                    forecastForStream.peak_month_date && forecastForStream.peak_rate != null
                      ? `${forecastForStream.peak_month_date.slice(0, 7)} · ${forecastForStream.peak_rate.toFixed(0)} ${units.rate}`
                      : "—"
                  }
                />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ParamRow({
  label,
  unit,
  value,
  onChange,
}: {
  label: string;
  unit: string;
  value: number | null;
  onChange: (v: number | null) => void;
}) {
  return (
    <tr>
      <th>{label}</th>
      <td>
        <input
          type="number"
          step="any"
          value={value ?? ""}
          onChange={(e) =>
            onChange(e.target.value === "" ? null : Number(e.target.value))
          }
        />
      </td>
      <td>
        <span className="muted">{unit}</span>
      </td>
    </tr>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

function fmtEur(eur: number | null, unit: string): string {
  if (eur == null) return "—";
  if (eur >= 1_000_000) return `${(eur / 1_000_000).toFixed(2)} M${unit}`;
  if (eur >= 1000) return `${(eur / 1000).toFixed(1)} k${unit}`;
  return `${eur.toFixed(0)} ${unit}`;
}
