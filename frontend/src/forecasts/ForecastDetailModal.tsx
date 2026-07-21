// Per-well detail modal: Cartesian + semi-log plots, editable params with
// live re-render against /api/forecasts/preview, manual-override lock,
// stream switcher (oil / gas / water).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type ForecastRow,
  type Stream,
  type StreamCurves,
  effectiveDecline,
  fetchWellCurves,
  patchForecast,
  previewForecast,
} from "../api/forecasts";
import {
  deleteForecastOverride,
  putForecastOverride,
} from "../api/typeCurves";
import { DeclineChart, type SeriesPoint } from "./DeclineChart";
import { runRevert } from "./revert";

interface TcContext {
  // Type curve the modal is editing FOR — Save writes a per-TC
  // override instead of patching the global forecast row.
  id: string;
  name: string;
  // Per-stream source flag from the workspace endpoint: "override" |
  // "global" | "missing". When "override", the modal surfaces a
  // "Revert to global" button. Keyed by stream so switching streams
  // inside the modal picks the right source.
  sourceByStream: Record<"oil" | "gas" | "water", "override" | "global" | "missing">;
  // Called after a successful override write OR delete with the
  // workspace's resolved stream payload (source + payload) so the
  // parent page can refresh its row without a second fetch.
  onOverrideChanged: (
    api10: string,
    stream: "oil" | "gas" | "water",
    source: "override" | "global" | "missing",
    payload: Record<string, unknown> | null,
  ) => void;
}

interface Props {
  api10: string;
  forecasts: ForecastRow[];
  onClose: () => void;
  onSaved: (updated: ForecastRow) => void;
  // Nav through the parent's current sort/filter ordering. Undefined
  // when at the boundary so the arrow disables. position is shown as
  // "N of M" so the reviewer knows where they are in the queue.
  onPrev?: () => void;
  onNext?: () => void;
  position?: { index: number; total: number };
  // Phase 1 of the TC workspace opens this modal in read-only mode:
  // charts + param values stay visible but the input boxes lock, the
  // "save override" / "lock" / "preview" buttons hide, and onSaved is
  // never called. Defaults false so every existing caller (Review tab,
  // etc.) keeps full editability.
  readOnly?: boolean;
  // Phase 2: when present, Save writes a TC-scoped override (not a
  // global forecast edit). The Revert-to-global button shows when
  // the active stream's source is "override". Undefined for the
  // Review tab's normal global-edit flow.
  tcContext?: TcContext;
}

const STREAM_UNITS: Record<Stream, { rate: string; cum: string }> = {
  oil: { rate: "BOPD", cum: "BBL" },
  gas: { rate: "MCFD", cum: "MCF" },
  water: { rate: "BWPD", cum: "BBL" },
};

export function ForecastDetailModal({
  api10,
  forecasts,
  onClose,
  onSaved,
  onPrev,
  onNext,
  position,
  readOnly = false,
  tcContext,
}: Props) {
  const [stream, setStream] = useState<Stream>("oil");
  const [curves, setCurves] = useState<StreamCurves | null>(null);
  const [loading, setLoading] = useState(true);
  // Linear-rate chart x-axis mode. Defaults to the observed window
  // (history + a small lookahead) so the actuals aren't compressed
  // into ~20 pixels by the 50-year forecast tail. Toggle flips to the
  // full forecast for the rare occasions you want to see the tail
  // approach the terminal Df decline. Log-rate + cum charts always
  // show the full range — see notes near the chart row.
  const [showFullRateAxis, setShowFullRateAxis] = useState(false);

  const forecastForStream = useMemo(
    () => forecasts.find((f) => f.stream === stream) ?? null,
    [forecasts, stream],
  );

  // Editable parameter state — initialized from the fitted forecast.
  // qo + peak_index_months let the user re-anchor the ramp prefix
  // (rate at first prod + ramp length to peak) when the auto-detector
  // landed wrong on a noisy well.
  const [editQi, setEditQi] = useState<number | null>(null);
  const [editDi, setEditDi] = useState<number | null>(null);
  const [editB, setEditB] = useState<number | null>(null);
  const [editDf, setEditDf] = useState<number | null>(null);
  const [editQo, setEditQo] = useState<number | null>(null);
  const [editPeakIndexMonths, setEditPeakIndexMonths] = useState<number | null>(null);
  const [previewPoints, setPreviewPoints] = useState<SeriesPoint[]>([]);
  const [previewCumPoints, setPreviewCumPoints] = useState<SeriesPoint[]>([]);
  const [previewEur, setPreviewEur] = useState<number | null>(null);
  // Method-1 displayed EUR + remaining for the preview-mode stat row.
  // Backend computes both server-side using the previewed params and
  // the well's actual cum / last-observed month. Falls back to the
  // saved forecast's eur_displayed / eur_remaining when no preview
  // is active.
  const [previewEurDisplayed, setPreviewEurDisplayed] = useState<number | null>(null);
  const [previewEurRemaining, setPreviewEurRemaining] = useState<number | null>(null);
  // Surfaced near the save/lock buttons when a persistence call fails.
  // Without this a rejected PATCH/PUT is indistinguishable from a
  // successful save — the user walks away believing the manual fit
  // persisted. Cleared at the start of the next save/lock attempt.
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    if (!forecastForStream) return;
    setEditQi(forecastForStream.params.qi ?? null);
    setEditDi(forecastForStream.params.Di ?? null);
    setEditB(forecastForStream.params.b ?? null);
    setEditDf(forecastForStream.params.Df ?? 0.08);
    // Prefer the explicit row columns over the JSONB params dict —
    // they're the columns the backfill + fit-time persistence write
    // to; the JSONB copies are denormalization for the evaluator.
    setEditQo(
      forecastForStream.qo ?? (forecastForStream.params.qo) ?? null,
    );
    setEditPeakIndexMonths(
      forecastForStream.peak_index_months ??
        (forecastForStream.params.peak_index_months) ??
        null,
    );
    setPreviewPoints([]);
    setPreviewCumPoints([]);
    setPreviewEur(null);
    setPreviewEurDisplayed(null);
    setPreviewEurRemaining(null);
    // Intentionally keyed only on the forecast's id: reset the edit
    // fields when the user switches to a DIFFERENT forecast, not on
    // every parent re-render (which would clobber in-progress edits).
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // Cancellation guard: rapid prev/next nav overlaps requests, and a
    // slower older response resolving last would overwrite `curves`
    // with the previous well's production under the new well's header.
    let cancelled = false;
    setLoading(true);
    // Pass tc_id when in TC context so any saved per-(api10,stream)
    // override on the type curve drives the forecast portion of the
    // chart. Without this the modal would draw the global fit and
    // "Save TC override" would appear to revert on the next refetch.
    void fetchWellCurves(api10, 50, tcContext?.id ?? null)
      .then((r) => {
        if (cancelled) return;
        const found = r.streams.find((s) => s.stream === stream) ?? null;
        setCurves(found);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api10, stream, tcContext?.id]);

  const history = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    return curves.history_rate
      .map((v, i) => (v == null ? null : { t: i, y: v }))
      .filter((p): p is SeriesPoint => p !== null);
  }, [curves]);

  // The backend now anchors forecast_months at first_prod_date when
  // the row has ramp params (qo / peak_index_months) — same anchor as
  // history — so the rate chart needs NO offset for the modern case.
  // For pre-ramp-migration rows the backend still anchors at
  // peak_month_date; the offset trick recovers the right alignment
  // for those by snapping forecast_months[0] to its index in the
  // history months array.
  const forecastOffset = useMemo(() => {
    if (!curves || curves.history_cum.length === 0) {
      return { idx: 0, cum: 0 };
    }
    const anchorMonth = curves.forecast_months[0];
    const idx = anchorMonth ? curves.months.indexOf(anchorMonth) : -1;
    const anchorIdx = idx >= 0 ? idx : Math.max(0, curves.history_cum.length - 1);
    return { idx: anchorIdx, cum: curves.history_cum[anchorIdx] ?? 0 };
  }, [curves]);

  const fitForecast = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    const { idx } = forecastOffset;
    return curves.forecast_rate.map((y, i) => ({ t: idx + i, y }));
  }, [curves, forecastOffset]);

  // Novi's forecast, mapped onto the same integer-month x-axis as
  // history/forecast (t = month index from curves.months[0]). Novi's
  // prod_dates are absolute, so we place each by month-delta from the
  // first history month. Empty when the well has no synced Novi series.
  const noviForecast = useMemo<SeriesPoint[]>(() => {
    if (!curves || curves.months.length === 0) return [];
    const base = curves.months[0];
    if (!base) return [];
    const monthDelta = (from: string, to: string) =>
      (new Date(to).getUTCFullYear() - new Date(from).getUTCFullYear()) * 12 +
      (new Date(to).getUTCMonth() - new Date(from).getUTCMonth());
    return curves.novi_months
      .map((m, i) => {
        const y = curves.novi_rate[i];
        return y == null ? null : { t: monthDelta(base, m), y };
      })
      .filter((p): p is SeriesPoint => p !== null);
  }, [curves]);

  const historyCum = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    return curves.history_cum
      .map((v, i) => (v == null ? null : { t: i, y: v }))
      .filter((p): p is SeriesPoint => p !== null);
  }, [curves]);

  const forecastCum = useMemo<SeriesPoint[]>(() => {
    if (!curves) return [];
    const { idx, cum } = forecastOffset;
    return curves.forecast_cum.map((y, i) => ({
      t: idx + i,
      y: y + cum,
    }));
  }, [curves, forecastOffset]);

  const displayedForecast = previewPoints.length > 0 ? previewPoints : fitForecast;
  const displayedForecastCum =
    previewCumPoints.length > 0 ? previewCumPoints : forecastCum;

  // Drag state. The modal is rendered as a free-floating panel (no
  // dark backdrop) so the user can keep it open while clicking
  // around the Review table behind it. Position is relative to the
  // panel's initial centered render — initial 0,0 means "wherever
  // the layout put me" and the user can drag from there.
  const [dragPos, setDragPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const dragOriginRef = useRef<{
    mouseX: number;
    mouseY: number;
    startX: number;
    startY: number;
  } | null>(null);

  const onDragHandleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      // Ignore drag on buttons / inputs inside the header (prev/next,
      // close) — only the header background area starts a drag.
      const target = e.target as HTMLElement;
      if (target.closest("button") || target.closest("input")) return;
      dragOriginRef.current = {
        mouseX: e.clientX,
        mouseY: e.clientY,
        startX: dragPos.x,
        startY: dragPos.y,
      };
      e.preventDefault();
    },
    [dragPos.x, dragPos.y],
  );

  useEffect(() => {
    function onMove(e: MouseEvent) {
      const o = dragOriginRef.current;
      if (!o) return;
      setDragPos({
        x: o.startX + (e.clientX - o.mouseX),
        y: o.startY + (e.clientY - o.mouseY),
      });
    }
    function onUp() {
      dragOriginRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  // Linear-rate chart x-axis cap. Defaults to history + 24 months so
  // the actuals dominate the view; when the user clicks "show full"
  // we fall back to undefined (the chart's auto-derived xMax over
  // history + forecast, i.e. the 50-year horizon).
  // Fallback minimum of 60 months handles brand-new wells where
  // history is just a few points — we want to show enough of the
  // forecast that the decline shape is visible even if there's
  // little history to anchor on.
  const linearRateXMax = useMemo<number | undefined>(() => {
    if (showFullRateAxis) return undefined;
    const lastHistoryMonth = history.length
      ? history[history.length - 1]!.t
      : 0;
    return Math.max(60, lastHistoryMonth + 24);
  }, [showFullRateAxis, history]);

  // ----------- live re-render on edits -----------
  async function recompute() {
    if (!forecastForStream) return;
    const params: Record<string, number> = {};
    if (editQi != null) params.qi = editQi;
    if (editDi != null) params.Di = editDi;
    if (editB != null) params.b = editB;
    if (editDf != null) params.Df = editDf;
    // Edited ramp prefix flows through too. The backend evaluates
    // ramp + Arps when both are present; either one missing falls
    // back to pure Arps from t=0.
    if (editQo != null) params.qo = editQo;
    if (editPeakIndexMonths != null) {
      params.peak_index_months = editPeakIndexMonths;
    }
    try {
      const p = await previewForecast({
        model_type: forecastForStream.model_type,
        params,
        // Monthly resolution across the 50-year horizon. The default
        // 120 points (one every ~5 months) skips over the ramp's
        // peak month entirely on most wells — qi edits then appear
        // to land at a clipped value because the chart's nearest
        // samples sit on either side of the actual peak.
        n_points: 600,
        // economic_limit omitted intentionally — this tool integrates
        // the full 50-yr horizon (technical EUR, no econ cutoff).
        // Pass api10 + stream so the backend can return Method-1
        // displayed EUR + remaining for the previewed params.
        api10,
        stream,
      });
      // Preview t_years is now measured from the row's anchor (first
      // prod when ramp params are present; peak otherwise). Use the
      // same forecastOffset the saved fit uses so preview and saved
      // line up exactly during the live overlay.
      const { idx, cum: anchorCum } = forecastOffset;
      const points = p.rate.map((y, i) => ({
        t: idx + (p.t_years[i] ?? 0) * 12,
        y,
      }));
      const cumPoints: SeriesPoint[] = p.cum.map((y, i) => ({
        t: idx + (p.t_years[i] ?? 0) * 12,
        y: y + anchorCum,
      }));
      setPreviewPoints(points);
      setPreviewCumPoints(cumPoints);
      setPreviewEur(p.eur);
      setPreviewEurDisplayed(p.eur_displayed);
      setPreviewEurRemaining(p.eur_remaining);
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
    if (editQo != null) params.qo = editQo;
    if (editPeakIndexMonths != null) {
      params.peak_index_months = editPeakIndexMonths;
    }
    setSaveError(null);

    if (tcContext) {
      // TC-context save: write a per-TC override. The global forecast
      // row stays untouched so other type curves containing this
      // well still see their previous fit. We DO refetch curves with
      // tc_id afterwards so the modal's chart reflects the override
      // (otherwise the red line snaps back to the global on the next
      // render — preview just clears, fitForecast falls back to the
      // unchanged /curves payload).
      try {
        const resolved = await putForecastOverride(
          tcContext.id,
          api10,
          stream,
          {
            qi: params.qi ?? editQi ?? 0,
            Di: params.Di ?? editDi ?? 0,
            b: params.b ?? editB ?? 0,
            Df: params.Df ?? editDf ?? 0.08,
            // Pass the ramp prefix when present so the override
            // preserves it. The backend stores it on the override
            // payload and /curves uses it when the modal refetches.
            qo: params.qo ?? editQo ?? null,
            peak_index_months:
              params.peak_index_months ?? editPeakIndexMonths ?? null,
          },
        );
        tcContext.onOverrideChanged(
          api10,
          stream,
          resolved.source,
          resolved.payload,
        );
      } catch (e) {
        // Keep the modal open and the user's edits intact — the
        // override did NOT persist and the message says so.
        setSaveError(e instanceof Error ? e.message : String(e));
        return;
      }
      setPreviewPoints([]);
      setPreviewCumPoints([]);
      setPreviewEur(null);
      setPreviewEurDisplayed(null);
      setPreviewEurRemaining(null);
      try {
        const r = await fetchWellCurves(api10, 50, tcContext.id);
        const found = r.streams.find((s) => s.stream === stream) ?? null;
        setCurves(found);
      } catch (e) {
        console.error("refetch curves after TC override failed", e);
      }
      return;
    }

    let updated: ForecastRow;
    try {
      updated = await patchForecast(forecastForStream.id, {
        params,
        manual_override: true,
      });
    } catch (e) {
      // Keep the modal open and the user's edits intact — the save
      // did NOT persist and the message says so.
      setSaveError(e instanceof Error ? e.message : String(e));
      return;
    }
    onSaved(updated);
    setPreviewPoints([]);
    setPreviewCumPoints([]);
    setPreviewEur(null);
    setPreviewEurDisplayed(null);
    setPreviewEurRemaining(null);
    // Refetch curves so fitForecast reflects the saved override. Without
    // this, clearing the preview overlay snaps the chart back to the
    // original auto-fit curve (cached in `curves` from the modal-open
    // fetch), making it look like the save reverted.
    try {
      const r = await fetchWellCurves(api10);
      const found = r.streams.find((s) => s.stream === stream) ?? null;
      setCurves(found);
    } catch (e) {
      console.error("refetch curves after save failed", e);
    }
  }

  async function revertOverride() {
    if (!tcContext) return;
    const ctx = tcContext;
    // A failed override-delete must surface (like save()/toggleLock()),
    // not silently look reverted. runRevert clears the error, deletes,
    // and only applies the reverted state on success; on failure it sets
    // saveError and returns false so we skip the refetch below.
    const ok = await runRevert({
      deleteOverride: () => deleteForecastOverride(ctx.id, api10, stream),
      applyResolved: (resolved) => {
        ctx.onOverrideChanged(api10, stream, resolved.source, resolved.payload);
        setPreviewPoints([]);
        setPreviewCumPoints([]);
        setPreviewEur(null);
        setPreviewEurDisplayed(null);
        setPreviewEurRemaining(null);
      },
      setError: setSaveError,
    });
    if (!ok) return;
    // Refetch with tc_id so the chart returns to the global fit (or
    // "missing" state) now that the override is gone.
    try {
      const r = await fetchWellCurves(api10, 50, ctx.id);
      const found = r.streams.find((s) => s.stream === stream) ?? null;
      setCurves(found);
    } catch (e) {
      console.error("refetch curves after revert failed", e);
    }
  }

  async function toggleLock() {
    if (!forecastForStream) return;
    setSaveError(null);
    try {
      const updated = await patchForecast(forecastForStream.id, {
        locked: !forecastForStream.locked,
      });
      onSaved(updated);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    }
  }

  const units = STREAM_UNITS[stream];

  return (
    <div className="modal-floating-wrap">
      <div
        className="modal modal-floating"
        style={{ transform: `translate(${dragPos.x}px, ${dragPos.y}px)` }}
      >
        <header
          className="modal-header modal-drag-handle"
          onMouseDown={onDragHandleMouseDown}
        >
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
            <strong style={{ marginLeft: 8 }}>{api10}</strong>
            {forecasts[0]?.well_name && (
              <span>— {forecasts[0].well_name}</span>
            )}
            <span className="muted">
              {forecastForStream?.model_type ?? "no forecast"}
            </span>
            {tcContext && (
              <span
                className="badge badge-warn"
                style={{ marginLeft: 8 }}
                title={`Save writes a per-TC override for ${tcContext.name}; the global forecast is unchanged.`}
              >
                editing override for {tcContext.name}
              </span>
            )}
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
            <div className="toolbar-group">
              {/* Affects the LINEAR rate chart only — log + cum charts
                  always show the full range since they're informative
                  across the whole forecast. */}
              <span className="toolbar-label">Rate axis:</span>
              <button
                type="button"
                className={`tb-btn ${!showFullRateAxis ? "tb-active" : ""}`}
                onClick={() => setShowFullRateAxis(false)}
                title="x-axis fits history + 24 months"
              >
                fit history
              </button>
              <button
                type="button"
                className={`tb-btn ${showFullRateAxis ? "tb-active" : ""}`}
                onClick={() => setShowFullRateAxis(true)}
                title="x-axis runs the full 50-year forecast horizon"
              >
                full forecast
              </button>
            </div>
          </div>

          {loading && <p className="muted">loading curves…</p>}

          <div className="chart-legend">
            <span className="chart-legend-item">
              <span
                className="chart-legend-swatch"
                style={{ background: "#1f2937" }}
              />
              Actual
            </span>
            <span className="chart-legend-item">
              <span
                className="chart-legend-swatch"
                style={{ background: "#dc2626" }}
              />
              App forecast
            </span>
            {noviForecast.length > 0 && (
              <span className="chart-legend-item">
                <span
                  className="chart-legend-swatch"
                  style={{ background: "#38bdf8" }}
                />
                Novi forecast
              </span>
            )}
          </div>

          <div className="chart-row">
            <DeclineChart
              history={history}
              forecast={displayedForecast}
              novi={noviForecast}
              yAxisType="linear"
              yLabel={units.rate}
              xLabel="Months from first production"
              xMaxOverride={linearRateXMax}
            />
            <DeclineChart
              history={history}
              forecast={displayedForecast}
              novi={noviForecast}
              yAxisType="log"
              yLabel={units.rate}
              xLabel="Months from first production"
            />
          </div>

          {/* Cum-vs-time row. Linear-Y only — log on a monotone cum
              curve doesn't help. The cum-forecast line is shifted by
              forecastOffset.idx so it lines up with history at the
              forecast's anchor month (first prod under the new ramp
              convention; peak under the old back-compat path).
              Param editor sits in the row's right cell so it uses
              the empty space next to the cum chart. */}
          <div className="chart-row">
            <DeclineChart
              history={historyCum}
              forecast={displayedForecastCum}
              yAxisType="linear"
              yLabel={units.cum}
              xLabel="Months from first production"
            />
          {forecastForStream && (
            <div className="param-editor">
              <h3>Fit parameters{readOnly && " (read-only)"}</h3>
              <table>
                <tbody>
                  <ParamRow
                    label="qo"
                    unit={units.rate}
                    value={editQo}
                    onChange={setEditQo}
                    readOnly={readOnly}
                  />
                  <ParamRow
                    label="peak month"
                    unit="mo"
                    value={editPeakIndexMonths}
                    onChange={setEditPeakIndexMonths}
                    readOnly={readOnly}
                  />
                  <ParamRow
                    label="qi"
                    unit={units.rate}
                    value={editQi}
                    onChange={setEditQi}
                    readOnly={readOnly}
                  />
                  <ParamRow
                    label="Di (nominal)"
                    unit="/yr"
                    value={editDi}
                    onChange={setEditDi}
                    readOnly={readOnly}
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
                    <ParamRow
                      label="b"
                      unit=""
                      value={editB}
                      onChange={setEditB}
                      readOnly={readOnly}
                    />
                  )}
                  {editDf != null && (
                    <ParamRow
                      label="Df (terminal)"
                      unit="/yr"
                      value={editDf}
                      onChange={setEditDf}
                      readOnly={readOnly}
                    />
                  )}
                </tbody>
              </table>
              {!readOnly && (
                <div className="param-actions">
                  <button type="button" onClick={() => void recompute()}>
                    preview
                  </button>
                  <button type="button" className="btn-primary" onClick={() => void save()}>
                    {tcContext ? "save TC override" : "save override"}
                  </button>
                  {tcContext &&
                    tcContext.sourceByStream[stream] === "override" && (
                      <button
                        type="button"
                        className="tb-btn"
                        onClick={() => void revertOverride()}
                        title="Delete the TC-scoped override and fall back to this well's global forecast"
                      >
                        revert to global
                      </button>
                    )}
                  {!tcContext && (
                    <button
                      type="button"
                      className={forecastForStream.locked ? "tb-active tb-btn" : "tb-btn"}
                      onClick={() => void toggleLock()}
                      title={
                        forecastForStream.locked
                          ? "Locked — bulk re-fit will skip this stream"
                          : "Unlocked — bulk re-fit will overwrite"
                      }
                    >
                      {forecastForStream.locked ? "locked" : "lock"}
                    </button>
                  )}
                </div>
              )}
              {saveError && (
                <div className="alert alert-error" style={{ marginTop: 4 }}>
                  save failed — changes not persisted: {saveError}
                </div>
              )}
              <div className="param-stats">
                {/* EUR shown is Method-1: actual cum to date + model's
                    projection from end-of-history to year 50. During
                    a live preview it uses the previewed-params
                    Method-1 EUR returned by the backend; otherwise
                    the saved row's eur_displayed. */}
                <Stat
                  label="EUR"
                  value={fmtEur(
                    previewEurDisplayed ??
                      forecastForStream.eur_displayed ??
                      previewEur ??
                      forecastForStream.eur,
                    units.cum,
                  )}
                />
                <Stat
                  label="Remaining"
                  value={fmtEur(
                    previewEurRemaining ?? forecastForStream.eur_remaining,
                    units.cum,
                  )}
                />
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
    </div>
  );
}

function ParamRow({
  label,
  unit,
  value,
  onChange,
  readOnly = false,
}: {
  label: string;
  unit: string;
  value: number | null;
  onChange: (v: number | null) => void;
  readOnly?: boolean;
}) {
  return (
    <tr>
      <th>{label}</th>
      <td>
        {readOnly ? (
          <span className="muted">{value == null ? "—" : value}</span>
        ) : (
          <input
            type="number"
            step="any"
            value={value ?? ""}
            onChange={(e) =>
              onChange(e.target.value === "" ? null : Number(e.target.value))
            }
          />
        )}
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
