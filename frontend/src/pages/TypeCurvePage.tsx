// Type Curve page: aggregate the currently included selection into a
// new curve, save / version / delete saved curves, export to CSV.

import type { CSSProperties } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  type AggregatePayload,
  type AlignmentMethod,
  type FitOverride,
  type StreamSeries,
  type TypeCurveRow,
  type TypeCurveSummary,
  computeTypeCurve,
  deleteTypeCurve,
  downloadTypeCurveExport,
  fetchTypeCurve,
  listTypeCurves,
  patchTypeCurve,
  previewTypeCurveFit,
  saveTypeCurve,
} from "../api/typeCurves";
import {
  type DealSummary,
  createDeal,
  deleteDeal,
  downloadDealExport,
  listDeals,
} from "../api/deals";
import { TypeCurveChart } from "../type_curves/TypeCurveChart";
import { TypeCurveLegend } from "../type_curves/TypeCurveLegend";
import { TypeCurveProbit } from "../type_curves/TypeCurveProbit";
import { effectiveDecline, nominalDecline } from "../api/forecasts";
import { useMapStore } from "../store/mapStore";

type Stream = "oil" | "gas" | "water";

const STREAM_UNITS: Record<Stream, { rate: string; cum: string }> = {
  oil: { rate: "BOPD / 1000 ft", cum: "BBL / 1000 ft" },
  gas: { rate: "MCFD / 1000 ft", cum: "MCF / 1000 ft" },
  water: { rate: "BWPD / 1000 ft", cum: "BBL / 1000 ft" },
};

const PERCENTILES: Array<{ key: string; label: string }> = [
  { key: "p10", label: "P10" },
  { key: "p25", label: "P25" },
  { key: "p50", label: "P50" },
  { key: "p75", label: "P75" },
  { key: "p90", label: "P90" },
  { key: "mean", label: "Mean" },
];

export function TypeCurvePage() {
  const forecastApi14s = useMapStore((s) => s.forecastApi14s);
  const excluded = useMapStore((s) => s.excludedApi14s);
  // Cohort-handoff prefill: when the user reached this page via the
  // cohort bar's Forecast button, these are populated. The save form
  // pre-fills its name input, and onSave auto-assigns the saved curve
  // to the cohort's deal so the engineer doesn't have to retype.
  const activeCohortName = useMapStore((s) => s.activeCohortName);
  const activeCohortDealId = useMapStore((s) => s.activeCohortDealId);

  const included = useMemo(
    () => forecastApi14s.filter((a) => !excluded.has(a)),
    [forecastApi14s, excluded],
  );

  const [agg, setAgg] = useState<AggregatePayload | null>(null);
  const [selectedSaved, setSelectedSaved] = useState<TypeCurveRow | null>(null);
  const [computing, setComputing] = useState(false);
  const [stream, setStream] = useState<Stream>("oil");
  const [alignment, setAlignment] = useState<AlignmentMethod>("first_prod_month");
  const [saveName, setSaveName] = useState("");
  const [saveNotes, setSaveNotes] = useState("");
  const [versionOf, setVersionOf] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [library, setLibrary] = useState<TypeCurveSummary[]>([]);
  const [compareWithId, setCompareWithId] = useState<string | null>(null);
  const [compareWith, setCompareWith] = useState<TypeCurveRow | null>(null);

  // Manual-tweak state, kept per stream (oil / gas / water) so switching
  // streams doesn't blow away in-flight edits.
  // editValues[stream] = current values in the form inputs (null = use
  //   the fitted defaults). Cleared by Reset, and when a fresh agg
  //   arrives.
  // previewSmoothed[stream] = the latest preview line received from
  //   /api/type-curves/preview. When non-null, it's drawn on every
  //   chart in place of the auto-fit smoothed_rate.
  // previewEur[stream] = EUR (BBL or MCF per 1000 ft) from the same
  //   preview response — shown in the Fitted P50 cell when active.
  const [editValues, setEditValues] = useState<Record<Stream, FitOverride | null>>({
    oil: null, gas: null, water: null,
  });
  const [previewSmoothed, setPreviewSmoothed] = useState<Record<Stream, number[] | null>>({
    oil: null, gas: null, water: null,
  });
  // Same draft fit, but evaluated client-side over 600 months — feeds
  // the full-forecast charts in the right column so they update in
  // sync with the data-window preview. Computed from buildRampArpsRate
  // so we don't need a second API round-trip; the math mirrors the
  // backend's evaluate_fit.
  const [previewFullSmoothed, setPreviewFullSmoothed] = useState<Record<Stream, number[] | null>>({
    oil: null, gas: null, water: null,
  });
  const [previewEur, setPreviewEur] = useState<Record<Stream, number | null>>({
    oil: null, gas: null, water: null,
  });
  const [tweakError, setTweakError] = useState<string | null>(null);

  // In-place edit state for the saved-curve panel.
  // editedNotes is the local textarea value; we PATCH on the explicit
  // Save button rather than on every keystroke so the user can stage
  // edits without spamming the API. updatingSaved guards both the notes
  // save and the Update fit button against double-submit.
  const [editedNotes, setEditedNotes] = useState<string>("");
  const [updatingSaved, setUpdatingSaved] = useState(false);
  const [updateError, setUpdateError] = useState<string | null>(null);

  const refreshLibrary = useCallback(async () => {
    setLibrary(await listTypeCurves());
  }, []);

  // Deals: a deal groups type curves so the engineer can export the
  // whole package as one Excel workbook. Cardinality is 1:N — each
  // curve has at most one deal via deal_id.
  const [deals, setDeals] = useState<DealSummary[]>([]);
  const [dealActionError, setDealActionError] = useState<string | null>(null);
  const refreshDeals = useCallback(async () => {
    setDeals(await listDeals());
  }, []);

  useEffect(() => {
    void refreshLibrary();
    void refreshDeals();
  }, [refreshLibrary, refreshDeals]);

  // Cohort-handoff prefill. Fires whenever saveName goes empty (initial
  // mount, after a successful save) and there's an active cohort name —
  // gives v2/v3/... of the same cohort the same pre-fill behavior as v1.
  useEffect(() => {
    if (activeCohortName && !saveName && forecastApi14s.length > 0) {
      setSaveName(activeCohortName);
    }
  }, [activeCohortName, saveName, forecastApi14s.length]);

  // Lazily load the comparison curve when the user picks one.
  useEffect(() => {
    if (!compareWithId) {
      setCompareWith(null);
      return;
    }
    let cancelled = false;
    fetchTypeCurve(compareWithId).then((tc) => {
      if (!cancelled) setCompareWith(tc);
    });
    return () => {
      cancelled = true;
    };
  }, [compareWithId]);

  // Auto-compute when arriving with an included selection, or when the
  // alignment changes. Each compute is cheap (a few hundred wells × a few
  // dozen months) so we just re-fire on every change.
  useEffect(() => {
    if (included.length === 0) {
      setAgg(null);
      return;
    }
    setComputing(true);
    setSelectedSaved(null);
    clearTweakState();
    computeTypeCurve({ api14s: included, alignment_method: alignment })
      .then(setAgg)
      .catch((e) => {
        console.error("compute failed", e);
        setAgg(null);
      })
      .finally(() => setComputing(false));
  }, [included.join(","), alignment]);

  function clearTweakState() {
    setEditValues({ oil: null, gas: null, water: null });
    setPreviewSmoothed({ oil: null, gas: null, water: null });
    setPreviewFullSmoothed({ oil: null, gas: null, water: null });
    setPreviewEur({ oil: null, gas: null, water: null });
    setTweakError(null);
  }

  // Gather per-stream overrides where the user has clicked Preview at
  // least once (so we don't persist tweak inputs the user never confirmed).
  function collectOverrides(): Record<string, FitOverride> | null {
    const out: Record<string, FitOverride> = {};
    for (const s of ["oil", "gas", "water"] as Stream[]) {
      const v = editValues[s];
      if (v && previewSmoothed[s]) {
        out[s] = v;
      }
    }
    return Object.keys(out).length > 0 ? out : null;
  }

  async function onSave() {
    if (!saveName.trim() || included.length === 0) return;
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await saveTypeCurve({
        name: saveName.trim(),
        notes: saveNotes.trim() || null,
        included_api14s: included,
        alignment_method: alignment,
        version_of: versionOf,
        fit_overrides: collectOverrides(),
      });
      // Auto-assign to the handoff deal if the cohort had one preset.
      // Verifies the deal still exists in case the user deleted it
      // between cohort creation and save.
      let final = saved;
      if (activeCohortDealId && deals.some((d) => d.id === activeCohortDealId)) {
        final = await patchTypeCurve(saved.id, { deal_id: activeCohortDealId });
      }
      setSaveName("");
      setSaveNotes("");
      setVersionOf(null);
      await refreshLibrary();
      setSelectedSaved(final);
      setAgg(final.series);
      clearTweakState();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function onTweakPreview() {
    if (!currentStream?.fitted || !agg) return;
    setTweakError(null);
    const draft = editValues[stream] ?? {
      qi: currentStream.fitted.qi,
      Di: currentStream.fitted.Di,
      b: currentStream.fitted.b,
      Df: currentStream.fitted.Df,
      qo: currentStream.fitted.qo ?? currentStream.fitted.qi,
      peak_index: currentStream.fitted.peak_index ?? 0,
    };
    setEditValues((prev) => ({ ...prev, [stream]: draft }));
    try {
      const p = await previewTypeCurveFit({
        ...draft,
        n_months: agg.n_months,
      });
      setPreviewSmoothed((prev) => ({ ...prev, [stream]: p.smoothed_rate }));
      setPreviewEur((prev) => ({ ...prev, [stream]: p.eur_per_unit }));
      // Evaluate the same draft fit out to 600 months for the right-
      // column charts. buildRampArpsRate mirrors the backend's
      // evaluate_fit, so the data-window and full-forecast preview
      // lines join smoothly at the n_months boundary.
      setPreviewFullSmoothed((prev) => ({
        ...prev,
        [stream]: buildRampArpsRate(draft, FULL_FORECAST_N_MONTHS),
      }));
    } catch (e) {
      setTweakError(e instanceof Error ? e.message : String(e));
    }
  }

  function onTweakReset() {
    setEditValues((prev) => ({ ...prev, [stream]: null }));
    setPreviewSmoothed((prev) => ({ ...prev, [stream]: null }));
    setPreviewFullSmoothed((prev) => ({ ...prev, [stream]: null }));
    setPreviewEur((prev) => ({ ...prev, [stream]: null }));
    setTweakError(null);
  }

  function setTweakField(key: keyof FitOverride, value: number) {
    if (!currentStream?.fitted) return;
    setEditValues((prev) => {
      const base: FitOverride = prev[stream] ?? {
        qi: currentStream.fitted!.qi,
        Di: currentStream.fitted!.Di,
        b: currentStream.fitted!.b,
        Df: currentStream.fitted!.Df,
        qo: currentStream.fitted!.qo ?? currentStream.fitted!.qi,
        peak_index: currentStream.fitted!.peak_index ?? 0,
      };
      return { ...prev, [stream]: { ...base, [key]: value } };
    });
  }

  async function onLoadSaved(id: string) {
    const row = await fetchTypeCurve(id);
    setSelectedSaved(row);
    setAgg(row.series);
    setAlignment(row.alignment_method);
    setEditedNotes(row.notes ?? "");
    setUpdateError(null);
    clearTweakState();
  }

  async function onRename(id: string) {
    const newName = window.prompt("New name for type curve:");
    if (!newName) return;
    await patchTypeCurve(id, { name: newName });
    await refreshLibrary();
    if (selectedSaved?.id === id) {
      setSelectedSaved(await fetchTypeCurve(id));
    }
  }

  async function onDelete(id: string) {
    if (!window.confirm("Delete this type curve?")) return;
    await deleteTypeCurve(id);
    if (selectedSaved?.id === id) {
      setSelectedSaved(null);
      setAgg(null);
      setEditedNotes("");
    }
    await refreshLibrary();
  }

  // Assign / un-assign the open saved curve to a deal. The select uses
  // three sentinel values: "" → un-assign, "__new__" → prompt for a
  // new deal name and create + assign, anything else → deal id.
  // The PATCH explicitly sends deal_id (including null) so the backend
  // can tell "un-assign" apart from "leave alone".
  async function onChangeDeal(value: string) {
    if (!selectedSaved) return;
    setDealActionError(null);
    try {
      let nextId: string | null = null;
      if (value === "__new__") {
        const name = window.prompt("Deal name:");
        if (!name || !name.trim()) return;
        const created = await createDeal({ name: name.trim() });
        nextId = created.id;
      } else if (value !== "") {
        nextId = value;
      }
      const updated = await patchTypeCurve(selectedSaved.id, {
        deal_id: nextId,
      });
      setSelectedSaved(updated);
      await Promise.all([refreshLibrary(), refreshDeals()]);
    } catch (e) {
      setDealActionError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onDeleteDeal(id: string, name: string) {
    if (!window.confirm(
      `Delete deal "${name}"? Assigned curves will be un-assigned (not deleted).`
    )) return;
    setDealActionError(null);
    try {
      await deleteDeal(id);
      await Promise.all([refreshLibrary(), refreshDeals()]);
      // If the open curve was in this deal, its deal_id is now null on
      // the server — re-fetch so the panel reflects that.
      if (selectedSaved && selectedSaved.deal_id === id) {
        setSelectedSaved(await fetchTypeCurve(selectedSaved.id));
      }
    } catch (e) {
      setDealActionError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onExportDeal(deal: DealSummary) {
    setDealActionError(null);
    try {
      const slug = deal.name.replace(/[^a-z0-9]+/gi, "_") || "deal";
      await downloadDealExport(deal.id, `${slug}.xlsx`);
    } catch (e) {
      setDealActionError(e instanceof Error ? e.message : String(e));
    }
  }

  // Persist the textarea's notes back to the saved curve. Trims trailing
  // whitespace; sends null when the textarea is empty so a previously-set
  // notes field can be cleared.
  async function onSaveNotes() {
    if (!selectedSaved) return;
    setUpdatingSaved(true);
    setUpdateError(null);
    try {
      const next = editedNotes.trim();
      const updated = await patchTypeCurve(selectedSaved.id, {
        notes: next.length > 0 ? next : null,
      });
      setSelectedSaved(updated);
      await refreshLibrary();
    } catch (e) {
      setUpdateError(e instanceof Error ? e.message : String(e));
    } finally {
      setUpdatingSaved(false);
    }
  }

  // Persist the active tweak-panel overrides back to the SAME saved
  // curve. The backend re-applies the overrides via _apply_fit_overrides
  // and returns the recomputed series, which we splice into agg so the
  // charts redraw from the new fit immediately. Only enabled when at
  // least one stream has a preview-active override (so we don't silently
  // overwrite a saved curve with the unchanged defaults).
  async function onUpdateFit() {
    if (!selectedSaved) return;
    const overrides = collectOverrides();
    if (!overrides) return;
    setUpdatingSaved(true);
    setUpdateError(null);
    try {
      const updated = await patchTypeCurve(selectedSaved.id, {
        fit_overrides: overrides,
      });
      setSelectedSaved(updated);
      setAgg(updated.series);
      clearTweakState();
      await refreshLibrary();
    } catch (e) {
      setUpdateError(e instanceof Error ? e.message : String(e));
    } finally {
      setUpdatingSaved(false);
    }
  }

  const currentStream = agg?.streams?.[stream];
  const units = STREAM_UNITS[stream];

  // Integrate the percentile rates to a cumulative-vs-time view. Cheap
  // (a few hundred months × six percentile arrays) so we just recompute
  // on every stream / agg change. The compare series and tweak-preview
  // override get the same treatment so the cum chart's overlays line up
  // with the rate charts.
  const cumStream = useMemo(
    () => (currentStream ? cumulateSeries(currentStream) : null),
    [currentStream],
  );
  const cumCompareSeries = useMemo(() => {
    const s = compareWith?.series.streams?.[stream];
    return s ? cumulateSeries(s) : null;
  }, [compareWith, stream]);
  const cumPreviewSmoothed = useMemo(() => {
    const p = previewSmoothed[stream];
    return p ? cumulateNumericArray(p) : null;
  }, [previewSmoothed, stream]);

  // Full-forecast (0–600 months) series for the right-column charts.
  // Each percentile is evaluated from its persisted Arps fit, so the
  // bands carry uncertainty out past the data window. The cum version
  // is just the time-integral of the full-forecast rates.
  const fullForecastStream = useMemo(
    () => (currentStream ? fullForecastSeries(currentStream) : null),
    [currentStream],
  );
  const fullForecastCumStream = useMemo(
    () => (fullForecastStream ? cumulateSeries(fullForecastStream) : null),
    [fullForecastStream],
  );
  const fullForecastCompareStream = useMemo(() => {
    const s = compareWith?.series.streams?.[stream];
    return s ? fullForecastSeries(s) : null;
  }, [compareWith, stream]);
  const fullForecastCumCompareStream = useMemo(
    () => (fullForecastCompareStream ? cumulateSeries(fullForecastCompareStream) : null),
    [fullForecastCompareStream],
  );
  // Preview-line overrides for the right-column charts. The rate line
  // is just the 600-month array we computed in onTweakPreview; the cum
  // line is its time-integral, same convention as cumPreviewSmoothed.
  const cumPreviewFullSmoothed = useMemo(() => {
    const p = previewFullSmoothed[stream];
    return p ? cumulateNumericArray(p) : null;
  }, [previewFullSmoothed, stream]);

  return (
    <div className="page page-two-col">
      <div className="type-curve-main">
        <header className="forecast-page-header">
          <strong>
            {selectedSaved ? selectedSaved.name : "New type curve"}
          </strong>
          <span className="muted">
            {agg ? `${agg.n_wells} wells · ${agg.n_months} months` : "—"}
          </span>
          <div className="toolbar-group">
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
          {computing && <span className="muted">computing…</span>}
        </header>

        {currentStream ? (
          <>
            <div className="chart-row" style={CHART_ROW_GRID_STYLE}>
              <TypeCurveChart
                series={currentStream}
                compareSeries={compareWith?.series.streams?.[stream] ?? null}
                compareLabel={compareWith?.name}
                yAxisType="linear"
                yLabel={units.rate}
                xLabel={xAxisLabel(selectedSaved?.alignment_method ?? alignment)}
                title="Cartesian"
                width={380}
                smoothedOverride={previewSmoothed[stream]}
              />
              <TypeCurveChart
                series={currentStream}
                compareSeries={compareWith?.series.streams?.[stream] ?? null}
                compareLabel={compareWith?.name}
                yAxisType="log"
                yLabel={units.rate}
                xLabel={xAxisLabel(selectedSaved?.alignment_method ?? alignment)}
                title="Semi-log (rate)"
                width={380}
                smoothedOverride={previewSmoothed[stream]}
              />
              {fullForecastStream && (
                <TypeCurveChart
                  series={fullForecastStream}
                  compareSeries={fullForecastCompareStream}
                  compareLabel={compareWith?.name}
                  yAxisType="log"
                  yLabel={units.rate}
                  xLabel={xAxisLabel(selectedSaved?.alignment_method ?? alignment)}
                  title="Full forecast — semi-log (rate)"
                  width={380}
                  smoothedOverride={previewFullSmoothed[stream]}
                />
              )}
            </div>

            {/* Row 2: early-time rate + data-window cum + full-forecast cum.
                Left two are the empirical / data-bounded views; the
                rightmost is the model-extrapolated cum out to 50 years
                where the asymptote should match the EUR table. */}
            <div className="chart-row" style={CHART_ROW_GRID_STYLE}>
              <TypeCurveChart
                series={currentStream}
                compareSeries={compareWith?.series.streams?.[stream] ?? null}
                compareLabel={compareWith?.name}
                yAxisType="linear"
                yLabel={units.rate}
                xLabel={xAxisLabel(selectedSaved?.alignment_method ?? alignment)}
                title="Early time (first 12 months)"
                xMaxMonths={12}
                xTickStep={1}
                width={380}
                smoothedOverride={previewSmoothed[stream]}
              />
              {cumStream && (
                <TypeCurveChart
                  series={cumStream}
                  compareSeries={cumCompareSeries}
                  compareLabel={compareWith?.name}
                  yAxisType="linear"
                  yLabel={units.cum}
                  xLabel={xAxisLabel(selectedSaved?.alignment_method ?? alignment)}
                  title="Cumulative (data window)"
                  width={380}
                  smoothedOverride={cumPreviewSmoothed}
                />
              )}
              {fullForecastCumStream && (
                <TypeCurveChart
                  series={fullForecastCumStream}
                  compareSeries={fullForecastCumCompareStream}
                  compareLabel={compareWith?.name}
                  yAxisType="linear"
                  yLabel={units.cum}
                  xLabel={xAxisLabel(selectedSaved?.alignment_method ?? alignment)}
                  title="Full forecast — cumulative"
                  width={380}
                  smoothedOverride={cumPreviewFullSmoothed}
                />
              )}
            </div>

            <TypeCurveLegend
              showCompare={!!compareWith}
              compareName={compareWith?.name ?? null}
            />

            <section className="filter-section type-curve-eur">
              <h3>
                Projected EUR per 1,000 lateral ft ({units.cum})
                <span
                  className="muted"
                  style={{ fontSize: 11, fontWeight: 400, marginLeft: 8 }}
                  title="Each cell: Arps fit to that percentile's rate series, projected to 50 years (or economic limit). Empty cell = series too short to fit."
                >
                  50-yr Arps projection per percentile
                </span>
              </h3>
              <table className="eur-table">
                <thead>
                  <tr>
                    {PERCENTILES.map((p) => (
                      <th key={p.key}>{p.label}</th>
                    ))}
                    <th title="Arps fit to P50 — the published curve">
                      Fitted P50
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    {PERCENTILES.map((p) => {
                      // Compute EUR from the persisted Arps params on the
                      // client using the same monthly integration path the
                      // Full-forecast cum chart uses. This guarantees the
                      // table value equals the chart's cum asymptote — and
                      // surfaces the technical (no-econ-cutoff) EUR even
                      // for curves saved before the backend defaults were
                      // flipped to no-cutoff. Falls back to the stored
                      // value when params for a percentile are missing
                      // (very old saves predate fitted_per_percentile).
                      const fit = currentStream.fitted_per_percentile?.[p.key];
                      const v =
                        eurFromParams(fit) ??
                        currentStream.fitted_eur_per_unit?.[p.key] ??
                        null;
                      return (
                        <td key={p.key}>
                          {v != null ? fmtEur(v) : "—"}
                        </td>
                      );
                    })}
                    <td>
                      {previewEur[stream] != null
                        ? fmtEur(previewEur[stream]!)
                        : currentStream.fitted
                          ? fmtEur(
                              eurFromParams(currentStream.fitted) ??
                                currentStream.fitted.eur_per_unit,
                            )
                          : "—"}
                    </td>
                  </tr>
                </tbody>
              </table>
              {currentStream.fitted && (
                <TweakPanel
                  stream={stream}
                  fitted={currentStream.fitted}
                  draft={editValues[stream]}
                  hasPreview={!!previewSmoothed[stream]}
                  error={tweakError}
                  onChange={setTweakField}
                  onPreview={onTweakPreview}
                  onReset={onTweakReset}
                />
              )}
            </section>

            {/* Per-well EUR/ft probit. Sits below the Preview/Tweak panel
                so it doesn't disrupt the multi-chart row layout above.
                Uses the same "active fit" (preview override → fitted →
                null) and the same Compare With curve as the rate/cum
                charts, so the vertical reference lines agree visually
                across the page. */}
            <section className="filter-section">
              <h3>
                Probit — {stream.charAt(0).toUpperCase() + stream.slice(1)} EUR / ft
                <span
                  className="muted"
                  style={{ fontSize: 11, fontWeight: 400, marginLeft: 8 }}
                  title="Lognormal probit of per-well 50-yr EUR / lateral_ft for the wells in scope. The vertical Type Curve line is the displayed fit's EUR / ft."
                >
                  per-well distribution vs. type curve
                </span>
              </h3>
              <TypeCurveProbit
                api14s={included}
                tcEurPerUnit={
                  previewEur[stream] ??
                  currentStream.fitted?.eur_per_unit ??
                  null
                }
                prevEurPerUnit={
                  compareWith?.series?.streams?.[stream]?.fitted?.eur_per_unit ??
                  null
                }
                prevLabel={compareWith?.name ?? null}
                stream={stream}
              />
            </section>
          </>
        ) : (
          <div className="muted" style={{ padding: 24 }}>
            {included.length === 0
              ? "No included wells. Forecast a selection and review it to populate this page."
              : computing
                ? "Computing…"
                : "No type curve to show."}
          </div>
        )}
      </div>

      <aside className="panel panel-right">
        <header className="panel-header">
          <span>{selectedSaved ? "Saved curve" : "Save new curve"}</span>
          {selectedSaved && (
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setSelectedSaved(null);
                setAgg(null);
              }}
            >
              clear
            </button>
          )}
        </header>

        <section className="filter-section">
          <h3>Alignment</h3>
          <select
            value={alignment}
            onChange={(e) => setAlignment(e.target.value as AlignmentMethod)}
            disabled={!!selectedSaved}
            title={
              selectedSaved
                ? "Alignment is fixed for saved curves — clear to recompute"
                : "First-prod includes ramp-up months (default for economics). Peak-month is for pure decline analysis."
            }
            style={{ width: "100%" }}
          >
            <option value="first_prod_month">
              First-prod month (incl. ramp-up)
            </option>
            <option value="peak_month">Peak month (decline-only)</option>
          </select>
        </section>

        {!selectedSaved && (
          <section className="filter-section">
            <input
              type="text"
              placeholder="curve name"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
            />
            <textarea
              placeholder="notes (optional)"
              value={saveNotes}
              onChange={(e) => setSaveNotes(e.target.value)}
              rows={3}
              style={{ width: "100%", marginTop: 6 }}
            />
            <label className="chk-inline" style={{ marginTop: 8 }}>
              save as new version of:
            </label>
            <select
              value={versionOf ?? ""}
              onChange={(e) => setVersionOf(e.target.value || null)}
              style={{ width: "100%" }}
            >
              <option value="">(standalone — not a version)</option>
              {library.map((tc) => (
                <option key={tc.id} value={tc.id}>
                  {tc.name}
                </option>
              ))}
            </select>
            {saveError && (
              <div className="alert alert-error" style={{ marginTop: 8 }}>
                {saveError}
              </div>
            )}
            <button
              type="button"
              className="btn-primary"
              disabled={!saveName.trim() || included.length === 0 || saving}
              onClick={onSave}
            >
              {saving ? "saving…" : `save ${included.length} wells`}
            </button>
          </section>
        )}

        {selectedSaved && (
          <section className="filter-section">
            <Stat label="Name" value={selectedSaved.name} />
            <label className="stat" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <span className="stat-label">Notes</span>
              <textarea
                value={editedNotes}
                onChange={(e) => setEditedNotes(e.target.value)}
                rows={3}
                placeholder="(no notes)"
                style={{ width: "100%", marginTop: 4 }}
              />
              {editedNotes !== (selectedSaved.notes ?? "") && (
                <button
                  type="button"
                  className="tb-btn"
                  onClick={onSaveNotes}
                  disabled={updatingSaved}
                  style={{ marginTop: 4, alignSelf: "flex-start" }}
                >
                  {updatingSaved ? "saving…" : "save notes"}
                </button>
              )}
            </label>
            <Stat
              label="Wells"
              value={selectedSaved.included_api14s.length.toString()}
            />
            <Stat
              label="Normalization"
              value={normalizationLabel(selectedSaved.normalization_basis)}
            />
            <Stat
              label="Alignment"
              value={alignmentLabel(selectedSaved.alignment_method)}
            />
            <Stat
              label="Created"
              value={new Date(selectedSaved.created_at).toLocaleString()}
            />
            {selectedSaved.version_of && (
              <Stat
                label="Version of"
                value={selectedSaved.version_of.slice(0, 8)}
              />
            )}
            <label className="stat" style={{ flexDirection: "column", alignItems: "stretch" }}>
              <span className="stat-label">Deal</span>
              <select
                value={selectedSaved.deal_id ?? ""}
                onChange={(e) => void onChangeDeal(e.target.value)}
                style={{ width: "100%", marginTop: 4 }}
              >
                <option value="">(unassigned)</option>
                {deals.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
                <option value="__new__">+ new deal…</option>
              </select>
            </label>
            {dealActionError && (
              <div className="alert alert-error" style={{ marginTop: 4 }}>
                {dealActionError}
              </div>
            )}
            <div className="param-actions" style={{ marginTop: 8 }}>
              <button
                type="button"
                className="btn-primary"
                onClick={onUpdateFit}
                disabled={!collectOverrides() || updatingSaved}
                title={
                  collectOverrides()
                    ? "Persist the previewed fit tweaks back to this saved curve"
                    : "Preview a tweak on a stream first to enable this"
                }
              >
                {updatingSaved ? "updating…" : "update fit"}
              </button>
              <button
                type="button"
                className="tb-btn"
                onClick={() =>
                  downloadTypeCurveExport(
                    selectedSaved.id,
                    `${selectedSaved.name.replace(/[^a-z0-9]+/gi, "_")}.zip`,
                  )
                }
              >
                export CSV
              </button>
              <button
                type="button"
                className="tb-btn"
                onClick={() => {
                  const qs = compareWithId
                    ? `?compareWith=${encodeURIComponent(compareWithId)}`
                    : "";
                  // Open in a new tab so the user's working state on
                  // this page survives the export. The slide route
                  // lives at the hash so it works without a router.
                  const url = `${window.location.origin}${window.location.pathname}#/type-curves/${encodeURIComponent(selectedSaved.id)}/slide${qs}`;
                  window.open(url, "_blank", "noopener");
                }}
                title="Open a print-ready slide of this type curve in a new tab"
              >
                export slide
              </button>
              <button
                type="button"
                className="tb-btn"
                onClick={() => onRename(selectedSaved.id)}
              >
                rename
              </button>
              <button
                type="button"
                className="tb-btn"
                onClick={() => onDelete(selectedSaved.id)}
                style={{ color: "#dc2626" }}
              >
                delete
              </button>
            </div>
            {updateError && (
              <div className="alert alert-error" style={{ marginTop: 8 }}>
                {updateError}
              </div>
            )}
          </section>
        )}

        <section className="filter-section">
          <h3>Compare with</h3>
          <select
            value={compareWithId ?? ""}
            onChange={(e) => setCompareWithId(e.target.value || null)}
            style={{ width: "100%" }}
          >
            <option value="">(none — single curve)</option>
            {library
              .filter((tc) => tc.id !== selectedSaved?.id)
              .map((tc) => (
                <option key={tc.id} value={tc.id}>
                  {tc.name}
                </option>
              ))}
          </select>
        </section>

        <section className="filter-section">
          <h3>Deals</h3>
          {deals.length === 0 ? (
            <p className="muted">No deals yet — assign a saved curve to a deal to create one.</p>
          ) : (
            <ul className="library-list">
              {deals.map((d) => (
                <li key={d.id} className="deal-row">
                  <div className="deal-row-info">
                    <div className="lib-name">{d.name}</div>
                    <div className="muted">{d.n_curves} curve{d.n_curves === 1 ? "" : "s"}</div>
                  </div>
                  <div className="deal-row-actions">
                    <button
                      type="button"
                      className="tb-btn"
                      onClick={() => void onExportDeal(d)}
                      disabled={d.n_curves === 0}
                      title={
                        d.n_curves === 0
                          ? "Assign curves to this deal before exporting"
                          : "Download .xlsx workbook for this deal"
                      }
                    >
                      export .xlsx
                    </button>
                    <button
                      type="button"
                      className="tb-btn"
                      onClick={() => void onDeleteDeal(d.id, d.name)}
                      style={{ color: "#dc2626" }}
                      title="Delete deal (curves stay)"
                    >
                      ×
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="filter-section">
          <h3>Library</h3>
          {library.length === 0 ? (
            <p className="muted">No saved curves yet.</p>
          ) : (
            <ul className="library-list">
              {library.map((tc) => (
                <li key={tc.id}>
                  <button
                    type="button"
                    className={selectedSaved?.id === tc.id ? "active" : ""}
                    onClick={() => onLoadSaved(tc.id)}
                  >
                    <div className="lib-name">{tc.name}</div>
                    <div className="muted">
                      {tc.n_wells} wells · {new Date(tc.created_at).toLocaleDateString()}
                      {tc.version_of && " · v"}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>
    </div>
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

// Map the DB-enum basis name to a unit-explicit human label. Keeps the
// stored value stable while making the panel read unambiguously
// (the actual rate unit is per-1,000-ft regardless of the basis name).
function normalizationLabel(basis: string): string {
  switch (basis) {
    case "per_lateral_ft": return "per 1,000 lateral ft";
    case "per_proppant_lb": return "per 1,000,000 lbs proppant";
    case "per_well": return "per well (un-normalized)";
    default: return basis;
  }
}

function alignmentLabel(method: string): string {
  switch (method) {
    case "first_prod_month": return "First-prod month (incl. ramp-up)";
    case "peak_month": return "Peak month (decline-only)";
    default: return method;
  }
}

function xAxisLabel(method: string): string {
  return method === "first_prod_month"
    ? "Months since first prod"
    : "Months since peak";
}

function fmtEur(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return v.toFixed(0);
}

// Average days per month for cum integration. The aggregated percentile
// rates are calendar-day rates (e.g. BOPD averaged across the month),
// so monthly volume = rate × days_per_month. Using the long-run average
// (365.25 / 12) keeps the cum from drifting against the per-well calday
// totals the backend would compute for an EUR.
const DAYS_PER_MONTH = 30.4375;

// Horizon for the full-forecast charts in the right column. 50 yr × 12 mo
// matches the backend's per-percentile evaluation horizon and the CSV
// export's fitted_forecast.csv so cums computed on either side of the
// API match to within trapezoid-vs-quad numerical noise.
//
// Note: this tool is intentionally a TECHNICAL type-curve generator.
// No economic-limit cutoff is applied anywhere — cum integrations run
// the full 50-yr horizon regardless of how low the modeled rate goes.
// Economics happens downstream on the exported workbook in the user's
// separate cash-flow tool.
const FULL_FORECAST_N_MONTHS = 600;

// Force CSS grid for the type-curve chart rows so the third (full-
// forecast) chart sits beside its siblings instead of wrapping under
// them. The default `.chart-row` class uses flex + flex-wrap, which
// pushes the third 420px child to a new line on anything less than
// a ~1700px-wide chart area — the override here keeps all three in a
// single visual row at the cost of a horizontal scroll on really
// narrow monitors. Min column width set so charts don't squish past
// readability if the right rail expands.
const CHART_ROW_GRID_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(3, minmax(380px, 1fr))",
  gap: 12,
};

// Mirror of backend `app.forecasting.models.modified_hyperbolic`:
// hyperbolic decline until instantaneous Di drops to Df, then exponential.
// Continuous at the switchover point. Used to extend the persisted
// per-percentile fits to the full 50-year horizon for the right-column
// charts (the persisted smoothed_rate only covers the data window).
function evalModifiedHyperbolicRate(
  tYears: number,
  qi: number,
  Di: number,
  b: number,
  Df: number,
): number {
  if (tYears <= 0) return qi;
  // Switchover: when instantaneous decline D(t) = b*Di/(1+b*Di*t) hits Df.
  // Solving for t gives t_s = (Di/Df - 1) / (b*Di). For b ≈ 0 or Df ≥ Di
  // the equation collapses; bail to pure hyperbolic / exponential.
  if (Df <= 0 || Di <= 0 || Df >= Di || Math.abs(b) < 1e-6) {
    return qi / Math.pow(1 + Math.max(b, 1e-6) * Di * tYears, 1 / Math.max(b, 1e-6));
  }
  const t_s = (Di / Df - 1) / (b * Di);
  if (tYears <= t_s) {
    return qi / Math.pow(1 + b * Di * tYears, 1 / b);
  }
  const q_s = qi * Math.pow(Df / Di, 1 / b);
  return q_s * Math.exp(-Df * (tYears - t_s));
}

// Minimal structural type — anything with these fields can be fed to
// buildRampArpsRate. Both FittedTypeCurve and FitOverride satisfy it.
interface RampArpsParams {
  qi: number;
  Di: number;
  b: number;
  Df: number;
  qo?: number;
  peak_index?: number;
}

// 50-yr cumulative integral of a fit, using the same monthly trapezoid
// the cum chart uses. Returns null if `fit` is null/undefined so the
// EUR-table cell can fall back to the stored value. Drives the EUR
// table directly so the displayed number can't drift from the cum
// chart's asymptote: same params → same integration → same number.
function eurFromParams(fit: RampArpsParams | null | undefined): number | null {
  if (!fit) return null;
  const rates = buildRampArpsRate(fit, FULL_FORECAST_N_MONTHS);
  let cum = 0;
  for (const r of rates) {
    if (Number.isFinite(r)) cum += r * DAYS_PER_MONTH;
  }
  return cum;
}

// Mirror of backend `app.type_curves.fit_p50.build_ramp_arps_rate`:
// linear ramp from Qo at month 0 up to qi at peak_index, then modified-
// hyperbolic decline. Returns a number[] of length nMonths.
function buildRampArpsRate(
  fit: RampArpsParams,
  nMonths: number,
): number[] {
  const out: number[] = [];
  const peakIndex = fit.peak_index ?? 0;
  const qo = fit.qo ?? fit.qi;
  // Linear-ramp prefix: months 0..peak_index inclusive, frac = i/peak_index.
  if (peakIndex > 0) {
    const ramp = Math.min(peakIndex + 1, nMonths);
    for (let i = 0; i < ramp; i++) {
      out.push(qo + (fit.qi - qo) * (i / peakIndex));
    }
  } else {
    out.push(fit.qi);
  }
  // Arps tail. Index peak_index + k (k = 1, 2, …) sits at t = k/12 yr
  // in the Arps coordinate system, matching the backend convention.
  const remaining = nMonths - out.length;
  for (let k = 1; k <= remaining; k++) {
    out.push(evalModifiedHyperbolicRate(k / 12, fit.qi, fit.Di, fit.b, fit.Df));
  }
  return out.slice(0, nMonths);
}

// Build a StreamSeries shaped for the right-column "full forecast"
// charts by evaluating each persisted percentile fit out to 50 years.
// The percentile bands then represent MODEL-extrapolated uncertainty
// across the full horizon (versus the empirical bands in the data-
// window charts on the left). Falls back to just the P50 fitted curve
// when fitted_per_percentile is missing (older saves predate that field).
const PERCENTILE_KEYS = ["p10", "p25", "p50", "p75", "p90"] as const;
function fullForecastSeries(stream: StreamSeries): StreamSeries {
  const N = FULL_FORECAST_N_MONTHS;
  const per = stream.fitted_per_percentile;
  const evalKey = (key: string): Array<number | null> => {
    const fit = per?.[key];
    if (!fit) return Array(N).fill(null);
    return buildRampArpsRate(fit, N);
  };
  return {
    ...stream,
    p10: evalKey("p10"),
    p25: evalKey("p25"),
    p50: evalKey("p50"),
    p75: evalKey("p75"),
    p90: evalKey("p90"),
    mean: evalKey("mean"),
    // well_count is meaningless past the data window — zero it so the
    // ribbon stays empty rather than projecting a stale count forward.
    well_count: Array(N).fill(0),
    fitted: stream.fitted
      ? { ...stream.fitted, smoothed_rate: buildRampArpsRate(stream.fitted, N) }
      : null,
  };
}

function cumulateNullableArray(arr: Array<number | null>): Array<number | null> {
  // Once a null appears, the cum series terminates — wells have dropped
  // out of the percentile cohort and any further "cum" would be an
  // artifact of flat-extrapolating from the last good month.
  const out: Array<number | null> = [];
  let running = 0;
  let stopped = false;
  for (const v of arr) {
    if (stopped || v == null || !Number.isFinite(v)) {
      stopped = true;
      out.push(null);
      continue;
    }
    running += v * DAYS_PER_MONTH;
    out.push(running);
  }
  return out;
}

function cumulateNumericArray(arr: number[]): number[] {
  // Fitted curves are sampled every month with no missing values, so
  // we keep the array tight (number[]) and just guard against the
  // pathological NaN that could come from a degenerate fit preview.
  const out: number[] = [];
  let running = 0;
  for (const v of arr) {
    if (!Number.isFinite(v)) {
      out.push(running);
      continue;
    }
    running += v * DAYS_PER_MONTH;
    out.push(running);
  }
  return out;
}

function cumulateSeries(s: StreamSeries): StreamSeries {
  return {
    ...s,
    p10: cumulateNullableArray(s.p10),
    p25: cumulateNullableArray(s.p25),
    p50: cumulateNullableArray(s.p50),
    p75: cumulateNullableArray(s.p75),
    p90: cumulateNullableArray(s.p90),
    mean: cumulateNullableArray(s.mean),
    fitted: s.fitted
      ? { ...s.fitted, smoothed_rate: cumulateNumericArray(s.fitted.smoothed_rate) }
      : null,
  };
}

interface TweakPanelProps {
  stream: Stream;
  fitted: NonNullable<AggregatePayload["streams"]["oil"]["fitted"]>;
  draft: FitOverride | null;
  hasPreview: boolean;
  error: string | null;
  onChange: (key: keyof FitOverride, value: number) => void;
  onPreview: () => void;
  onReset: () => void;
}

function TweakPanel({
  stream,
  fitted,
  draft,
  hasPreview,
  error,
  onChange,
  onPreview,
  onReset,
}: TweakPanelProps) {
  // When the user hasn't edited yet, show the fitted values in the
  // inputs (so they have something to edit). Once they start editing,
  // draft holds the working copy.
  const v = draft ?? {
    qi: fitted.qi,
    Di: fitted.Di,
    b: fitted.b,
    Df: fitted.Df,
    qo: fitted.qo ?? fitted.qi,
    peak_index: fitted.peak_index ?? 0,
  };
  // Di is stored in nominal units (what Arps eqn consumes) but engineers
  // think and talk in first-year effective decline %. We render the input
  // as effective % and convert back to nominal on every keystroke.
  const diEffectivePct = effectiveDecline(v.Di, v.b) * 100;
  const isOverride = fitted.manual_override === true;
  return (
    <div className="fitted-params tweak-panel">
      <div className="tweak-header">
        <strong>Fit ({stream}):</strong>
        {isOverride && (
          <span className="badge badge-warn" title="Loaded from a manual override">
            manual
          </span>
        )}
        {hasPreview && (
          <span className="badge badge-warn" title="Preview is active — Save to persist">
            preview active
          </span>
        )}
      </div>
      <div className="tweak-grid">
        <TweakInput
          label="Qo"
          unit={stream === "oil" ? "BOPD" : stream === "gas" ? "MCFD" : "BWPD"}
          value={v.qo}
          step={1}
          onChange={(n) => onChange("qo", n)}
          title="Initial rate at month 0 (first prod). Sets the ramp's left endpoint."
        />
        <TweakInput
          label="peak_idx"
          unit="mo"
          value={v.peak_index}
          step={1}
          onChange={(n) => onChange("peak_index", Math.max(0, Math.round(n)))}
          title="Month index where ramp ends and Arps decline begins. 0 = no ramp."
        />
        <TweakInput
          label="qi"
          unit={stream === "oil" ? "BOPD" : stream === "gas" ? "MCFD" : "BWPD"}
          value={v.qi}
          step={1}
          onChange={(n) => onChange("qi", n)}
          title="Peak rate — start of Arps decline."
        />
        <TweakInput
          label="Di"
          unit="% eff"
          value={Math.round(diEffectivePct * 10) / 10}
          step={1}
          onChange={(n) => onChange("Di", nominalDecline(n / 100, v.b))}
          title={`First-year effective decline (industry convention). Nominal Di used in the Arps formula: ${v.Di.toFixed(2)} /yr at b=${v.b.toFixed(2)}.`}
        />
        <TweakInput
          label="b"
          unit=""
          value={v.b}
          step={0.05}
          onChange={(n) => onChange("b", n)}
          title="Arps hyperbolic exponent. Permian-typical: 0.7–1.5."
        />
        <TweakInput
          label="Df"
          unit="/yr"
          value={v.Df}
          step={0.01}
          onChange={(n) => onChange("Df", n)}
          title="Terminal exponential decline."
        />
      </div>
      <div className="tweak-actions">
        <button type="button" onClick={onPreview} className="btn-primary">
          Preview
        </button>
        <button type="button" onClick={onReset} disabled={!draft && !hasPreview}>
          Reset
        </button>
        {error && <span className="muted err">{error}</span>}
      </div>
    </div>
  );
}

function TweakInput({
  label, unit, value, step, onChange, title,
}: {
  label: string;
  unit: string;
  value: number;
  step: number;
  onChange: (n: number) => void;
  title: string;
}) {
  return (
    <label className="tweak-input" title={title}>
      <span className="tweak-label">{label}</span>
      <input
        type="number"
        step={step}
        value={Number.isFinite(value) ? value : ""}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
      />
      {unit && <span className="tweak-unit muted">{unit}</span>}
    </label>
  );
}
