// Type Curve workspace — Phase 1, read-only navigation.
//
// Opens from a saved Type Curve's library card via the "wells →" link.
// Shows the wells the TC was derived from + each well's resolved
// forecast per stream (override → global → missing fallback) so the
// engineer can QC underlying fits. Clicking a row opens the per-well
// ForecastDetailModal in read-only mode.
//
// Phase 2 will swap read-only off, add include/exclude + "add wells",
// and wire a "Re-run aggregation" button into the stale banner.

import { useEffect, useMemo, useState } from "react";

import {
  type ForecastRow,
  type Stream,
  effectiveDecline,
} from "../api/forecasts";
import {
  type StreamSeries,
  type TypeCurveRow,
  type WorkspaceWell,
  fetchTypeCurve,
  fetchTypeCurveWorkspaceWells,
  patchTypeCurveMembership,
  reaggregateTypeCurve,
} from "../api/typeCurves";
import { ForecastDetailModal } from "../forecasts/ForecastDetailModal";
import {
  Th,
  fmtDi,
  fmtInt,
  fmtVol,
  type SortDir,
} from "../forecasts/reviewTable";
import { TypeCurveChart } from "../type_curves/TypeCurveChart";

interface Props {
  typeCurveId: string;
  onExit: () => void;
}

type SortKey =
  | "api14"
  | "well_name"
  | "formation"
  | "operator"
  | "first_prod_date"
  | "lateral"
  | "oil_eur"
  | "oil_eur_per_ft"
  | "oil_di"
  | "gas_di"
  | "water_di"
  | "source";

const STREAMS: Stream[] = ["oil", "gas", "water"];

// Pull the 1-yr effective decline out of an override-or-global
// payload. Mirrors effDiFor in reviewTable.tsx but consumes the
// looser ``Record<string, unknown>`` shape the workspace endpoint
// returns (overrides may skip the server-emitted di_effective).
function diFromPayload(
  payload: Record<string, unknown> | null | undefined,
): number | null {
  if (!payload) return null;
  const params = (payload.params as Record<string, unknown> | null) ?? null;
  const diRaw = params?.Di ?? payload.di_initial;
  const bRaw = params?.b ?? payload.b;
  const di = typeof diRaw === "number" && Number.isFinite(diRaw) ? diRaw : null;
  const b = typeof bRaw === "number" && Number.isFinite(bRaw) ? bRaw : null;
  if (di == null) return null;
  return effectiveDecline(di, b);
}

function numFromPayload(
  payload: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  if (!payload) return null;
  const v = payload[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// Build the synthetic ForecastRow array the modal expects. The
// workspace endpoint already returned a per-stream payload close to
// the ForecastRow shape — this projects it into the exact interface.
function buildModalForecasts(row: WorkspaceWell): ForecastRow[] {
  const out: ForecastRow[] = [];
  for (const stream of STREAMS) {
    const slot = row[stream];
    const p = slot.payload;
    if (!p) continue;
    const params = (p.params as Record<string, number>) ?? {};
    const di = numFromPayload(p, "di_initial");
    const b = numFromPayload(p, "b");
    out.push({
      id: (p.id as string) ?? `${row.api14}-${stream}-virtual`,
      api14: row.api14,
      stream,
      model_type:
        ((p.model_type as ForecastRow["model_type"]) ?? "modified_hyperbolic"),
      params,
      qi: numFromPayload(p, "qi"),
      di_initial: di,
      di_effective: di == null ? null : effectiveDecline(di, b),
      b,
      df_terminal: numFromPayload(p, "df_terminal"),
      eur: numFromPayload(p, "eur"),
      peak_month_date: (p.peak_month_date as string | null) ?? null,
      peak_rate: numFromPayload(p, "peak_rate"),
      fit_method: ((p.fit_method as ForecastRow["fit_method"]) ?? "rate_cum"),
      fit_r2: numFromPayload(p, "fit_r2"),
      fit_rmse: numFromPayload(p, "fit_rmse"),
      fit_at_bound: false,
      bound_note: null,
      downtime_ratio: numFromPayload(p, "downtime_ratio"),
      manual_override: (p.manual_override as boolean) ?? false,
      locked: (p.locked as boolean) ?? false,
      updated_at: new Date().toISOString(),
      well_name: row.well_name,
      well_operator: row.well_operator,
      well_formation: row.well_formation,
      well_lateral_ft: row.well_lateral_ft,
      well_vintage_year: null,
      well_first_prod_date: row.well_first_prod_date,
      well_county: row.well_county,
    });
  }
  return out;
}

// Roll up the per-stream source flags into one badge per row. Phase 2
// will surface this in the UI when overrides exist; in Phase 1 every
// row reads "global" since nothing writes overrides yet.
function rowSource(w: WorkspaceWell): "override" | "global" | "missing" {
  const sources = STREAMS.map((s) => w[s].source);
  if (sources.includes("override")) return "override";
  if (sources.every((s) => s === "missing")) return "missing";
  return "global";
}

export function TypeCurveWellsPage({ typeCurveId, onExit }: Props) {
  const [tc, setTc] = useState<TypeCurveRow | null>(null);
  const [wells, setWells] = useState<WorkspaceWell[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [sortKey, setSortKey] = useState<SortKey>("oil_eur_per_ft");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [openApi14, setOpenApi14] = useState<string | null>(null);
  const [qcStream, setQcStream] = useState<Stream>("oil");

  async function refresh(): Promise<void> {
    const [tcRow, wellRows] = await Promise.all([
      fetchTypeCurve(typeCurveId),
      fetchTypeCurveWorkspaceWells(typeCurveId),
    ]);
    setTc(tcRow);
    setWells(wellRows);
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    refresh()
      .catch((e) => {
        if (!cancelled) setErr(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // refresh is closed over typeCurveId only; intentionally not in deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeCurveId]);

  async function excludeWell(api14: string): Promise<void> {
    setBusy(`exclude:${api14}`);
    setErr(null);
    try {
      // If the modal is open on the well we're dropping, close it —
      // the row will vanish on refresh.
      if (openApi14 === api14) setOpenApi14(null);
      await patchTypeCurveMembership(typeCurveId, { remove: [api14] });
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function rerunAggregation(): Promise<void> {
    setBusy("reaggregate");
    setErr(null);
    try {
      await reaggregateTypeCurve(typeCurveId);
      await refresh();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(null);
    }
  }

  // Modal hook: when an override is written or reverted, splice the
  // new resolved stream into the wells array in place. Saves a full
  // workspace refetch and keeps the sort order stable.
  function handleOverrideChanged(
    api14: string,
    stream: Stream,
    source: "override" | "global" | "missing",
    payload: Record<string, unknown> | null,
  ) {
    setWells((prev) =>
      prev
        ? prev.map((w) =>
            w.api14 === api14
              ? { ...w, [stream]: { source, payload } }
              : w,
          )
        : prev,
    );
  }

  const sorted = useMemo(() => {
    if (!wells) return [];
    const sign = sortDir === "asc" ? 1 : -1;
    const get = (w: WorkspaceWell): number | string => {
      switch (sortKey) {
        case "api14": return w.api14;
        case "well_name": return w.well_name ?? "";
        case "formation": return w.well_formation ?? "";
        case "operator": return w.well_operator ?? "";
        case "first_prod_date": return w.well_first_prod_date ?? "";
        case "lateral": return w.well_lateral_ft ?? 0;
        case "oil_eur": return numFromPayload(w.oil.payload, "eur") ?? -1;
        case "oil_eur_per_ft": {
          const eur = numFromPayload(w.oil.payload, "eur");
          return eur != null && w.well_lateral_ft
            ? eur / w.well_lateral_ft
            : -1;
        }
        case "oil_di": return diFromPayload(w.oil.payload) ?? -1;
        case "gas_di": return diFromPayload(w.gas.payload) ?? -1;
        case "water_di": return diFromPayload(w.water.payload) ?? -1;
        case "source": return rowSource(w);
      }
    };
    return [...wells].sort((a, b) => {
      const av = get(a), bv = get(b);
      if (typeof av === "string") return sign * av.localeCompare(bv as string);
      return sign * ((av as number) - (bv as number));
    });
  }, [wells, sortKey, sortDir]);

  function clickHeader(key: SortKey) {
    if (key === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const openWell = openApi14 ? wells?.find((w) => w.api14 === openApi14) : null;
  const openIndex = openWell ? sorted.findIndex((w) => w.api14 === openApi14) : -1;
  const onPrev =
    openIndex > 0 ? () => setOpenApi14(sorted[openIndex - 1]!.api14) : undefined;
  const onNext =
    openIndex >= 0 && openIndex < sorted.length - 1
      ? () => setOpenApi14(sorted[openIndex + 1]!.api14)
      : undefined;

  // Override census: count source==="override" cells across streams +
  // distinct wells with at least one override. Drives the info strip.
  const overrideStats = useMemo(() => {
    if (!wells) return { total: 0, wells: 0 };
    let total = 0;
    let wellsWith = 0;
    for (const w of wells) {
      const n = STREAMS.filter((s) => w[s].source === "override").length;
      if (n > 0) {
        total += n;
        wellsWith += 1;
      }
    }
    return { total, wells: wellsWith };
  }, [wells]);

  // QC overlay: empirical bands (from series.observed_streams) with the
  // forecast-aggregated fitted P50 overlaid. Lets the engineer see
  // "what wells actually produced" vs "what we'll publish" within the
  // observation window. None on pre-Phase-2.5 saves (no observed_streams
  // block persisted).
  const qcSeries = useMemo<StreamSeries | null>(() => {
    if (!tc?.series?.observed_streams) return null;
    const obs = tc.series.observed_streams[qcStream];
    if (!obs || !obs.p50 || obs.p50.length === 0) return null;
    const forecast = tc.series.streams[qcStream];
    const obsLen = obs.p50.length;
    // Trim the forecast fitted line to the observed window so the
    // overlay aligns visually with the bands. The fit's smoothed_rate
    // is sized to the forecast horizon (600 mo); we only want the
    // first obsLen months on this chart.
    const fittedForOverlay = forecast?.fitted
      ? {
          ...forecast.fitted,
          smoothed_rate: forecast.fitted.smoothed_rate.slice(0, obsLen),
        }
      : null;
    return {
      ...obs,
      fitted: fittedForOverlay,
    };
  }, [tc, qcStream]);
  const qcXMaxMonths = qcSeries?.p50.length ?? null;

  return (
    <div className="page">
      <header className="review-header" style={{ gap: 12 }}>
        <button type="button" className="link-btn" onClick={onExit}>
          ← back to Type Curve
        </button>
        <strong>{tc?.name ?? "…"}</strong>
        <span className="muted">
          {wells != null ? `${wells.length} wells` : ""}
        </span>
        {loading && <span className="muted">loading…</span>}
        {err && <span className="alert alert-error">{err}</span>}
        <span style={{ flex: 1 }} />
        <a
          className="link-btn"
          href={`#/type-curves/${typeCurveId}/add-wells`}
          title="Open the Map tab with this TC's filter pre-applied so you can stage and add new wells"
        >
          add wells →
        </a>
      </header>

      {overrideStats.total > 0 && (
        <div
          className="alert"
          style={{
            background: "#eff6ff",
            border: "1px solid #93c5fd",
            color: "#1e3a8a",
            padding: "8px 12px",
            margin: "8px 16px",
          }}
        >
          {overrideStats.total} per-well override
          {overrideStats.total === 1 ? "" : "s"} applied across{" "}
          {overrideStats.wells} well{overrideStats.wells === 1 ? "" : "s"}.{" "}
          {tc?.is_stale
            ? "Re-run aggregation (banner above) to fold them into the bands + fitted P50."
            : "The aggregated bands and fitted P50 already reflect these overrides."}
        </div>
      )}
      {tc?.is_stale && (
        <div
          className="alert"
          style={{
            background: "#fef3c7",
            border: "1px solid #f59e0b",
            color: "#78350f",
            padding: "8px 12px",
            margin: "8px 16px",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <span style={{ flex: 1 }}>
            Underlying inputs have changed since the last aggregation —
            overrides, membership, or both. The bands + fitted P50 are
            out of date.
          </span>
          <button
            type="button"
            className="btn-primary"
            disabled={busy != null}
            onClick={rerunAggregation}
          >
            {busy === "reaggregate" ? "running…" : "Re-run aggregation"}
          </button>
        </div>
      )}

      {qcSeries && qcXMaxMonths != null && (
        <section
          className="filter-section"
          style={{ margin: "8px 16px", padding: "10px 12px" }}
        >
          <h3 style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span>Observed vs forecast (QC)</span>
            <span className="muted" style={{ fontSize: 11, fontWeight: 400 }}>
              empirical bands from production_monthly; bold line is the
              forecast-aggregated fitted P50
            </span>
            <span style={{ flex: 1 }} />
            {STREAMS.map((s) => (
              <button
                key={s}
                type="button"
                className={`tb-btn ${qcStream === s ? "tb-active" : ""}`}
                onClick={() => setQcStream(s)}
              >
                {s}
              </button>
            ))}
          </h3>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <TypeCurveChart
              series={qcSeries}
              yAxisType="log"
              yLabel={qcStream === "gas" ? "MCFD / 1000 ft" : "BBL/d / 1000 ft"}
              xLabel="Months since first prod"
              xMaxMonths={qcXMaxMonths}
              title={`${qcStream.toUpperCase()} — rate`}
              width={480}
              height={280}
            />
          </div>
        </section>
      )}

      <div className="review-table" style={{ margin: "0 16px" }}>
        <table>
          <thead>
            <tr>
              <Th k="api14" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                api14
              </Th>
              <Th k="well_name" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                well name
              </Th>
              <Th k="formation" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                formation
              </Th>
              <Th k="operator" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                operator
              </Th>
              <Th k="first_prod_date" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                First Prod Date
              </Th>
              <Th k="lateral" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                lateral (ft)
              </Th>
              <Th k="oil_eur" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                Oil EUR (BBL)
              </Th>
              <Th k="oil_eur_per_ft" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                Oil EUR / ft
              </Th>
              <Th k="oil_di" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                Oil Di
              </Th>
              <Th k="gas_di" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                Gas Di
              </Th>
              <Th k="water_di" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                Water Di
              </Th>
              <Th k="source" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                source
              </Th>
              <th>exclude</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((w) => {
              const eurOil = numFromPayload(w.oil.payload, "eur");
              const eurPerFt =
                eurOil != null && w.well_lateral_ft
                  ? eurOil / w.well_lateral_ft
                  : null;
              const src = rowSource(w);
              return (
                <tr
                  key={w.api14}
                  className="row-clickable"
                  onClick={() => setOpenApi14(w.api14)}
                >
                  <td>{w.api14}</td>
                  <td>{w.well_name ?? "—"}</td>
                  <td>{w.well_formation ?? "—"}</td>
                  <td>{w.well_operator ?? "—"}</td>
                  <td>{w.well_first_prod_date ?? "—"}</td>
                  <td>{fmtInt(w.well_lateral_ft)}</td>
                  <td>{fmtVol(eurOil)}</td>
                  <td>{eurPerFt != null ? eurPerFt.toFixed(1) : "—"}</td>
                  <td>{fmtDi(diFromPayload(w.oil.payload))}</td>
                  <td>{fmtDi(diFromPayload(w.gas.payload))}</td>
                  <td>{fmtDi(diFromPayload(w.water.payload))}</td>
                  <td>
                    {src === "override" && (
                      <span className="badge badge-warn">override</span>
                    )}
                    {src === "missing" && (
                      <span className="badge badge-err">missing</span>
                    )}
                    {src === "global" && <span className="muted">global</span>}
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="link-btn"
                      disabled={busy != null}
                      style={{ color: "#dc2626" }}
                      title="Remove this well from the type curve. Bands won't update until you click Re-run aggregation."
                      onClick={() => excludeWell(w.api14)}
                    >
                      {busy === `exclude:${w.api14}` ? "removing…" : "remove"}
                    </button>
                  </td>
                </tr>
              );
            })}
            {!loading && sorted.length === 0 && (
              <tr>
                <td colSpan={13} className="muted" style={{ textAlign: "center" }}>
                  No wells in this type curve.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {openWell && tc && (
        <ForecastDetailModal
          api14={openWell.api14}
          forecasts={buildModalForecasts(openWell)}
          onClose={() => setOpenApi14(null)}
          onSaved={() => {
            /* Saves in TC context go through onOverrideChanged below. */
          }}
          onPrev={onPrev}
          onNext={onNext}
          position={
            openIndex >= 0
              ? { index: openIndex + 1, total: sorted.length }
              : undefined
          }
          tcContext={{
            id: tc.id,
            name: tc.name,
            sourceByStream: {
              oil: openWell.oil.source,
              gas: openWell.gas.source,
              water: openWell.water.source,
            },
            onOverrideChanged: handleOverrideChanged,
          }}
        />
      )}
    </div>
  );
}

