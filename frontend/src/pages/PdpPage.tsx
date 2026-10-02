// PDP review tab: forecasts fit on seller data-room DAILY production
// (backend app/forecasting/daily.py), deal-scoped in pdp_forecasts —
// never the global forecasts table.
//
// Flow: pick deal -> pick data room (saved to deals.pdp_config) -> Sync
// (copy the room's daily rows) -> Run forecast -> review each well:
// set a fit window (click the chart), override Di/b, override uptime,
// lock. Manual params and windows survive every re-run; locked streams
// are skipped by re-runs.
//
// Conventions shown on screen: Di is NOMINAL per year, always with the
// 1-yr effective alongside (anchor = from the params' t = 0; forward =
// what the forecast does in the year after data_through). Rates are
// producing-day; volumes are calendar (x uptime). Remaining = from
// data_through to first prod + 50 yr (raw technical integral, no
// economic limit).

import { useCallback, useEffect, useMemo, useState } from "react";

import { listDeals, type DealSummary } from "../api/deals";
import {
  effectiveFromNominal,
  fetchPdpSeries,
  getPdpConfig,
  listPdpForecasts,
  listVdrSources,
  patchPdpForecast,
  putPdpConfig,
  putWellUptime,
  runPdpForecast,
  syncPdp,
  type PdpPatch,
  type PdpRow,
  type PdpSeries,
  type PdpStream,
  type VdrSource,
  STREAM_COLOR,
} from "../api/pdp";
import { PdpChart } from "../components/PdpChart";
import { PdpExportPanel } from "../components/PdpExportPanel";
import { useMapStore } from "../store/mapStore";

const STREAMS: PdpStream[] = ["oil", "gas", "water"];

const FLAG_TEXT: Record<string, string> = {
  unpeaked_transfer:
    "Still inclining — declines now on the same-bench cohort Di",
  at_bound: "A fit parameter sits on its bound",
  tail_mismatch: "Model vs last-90-day actual outside ±15%",
  recent_break: "Shut-in or choke change in the last 18 months",
  donor_water_allocated: "Donor water is Novi TX allocation (0.970 × gas)",
  shut_in:
    "No producing day in the last 365 d — forecast zero; set manual params with a future anchor for a restart",
};

const FLAG_SHORT: Record<string, string> = {
  unpeaked_transfer: "unpeaked",
  tail_mismatch: "tail",
  recent_break: "break",
  donor_water_allocated: "donor H₂O",
  shut_in: "shut-in",
};

const METHOD_LABEL: Record<string, string> = {
  daily_fit: "fit",
  transfer_now: "cohort",
  manual: "manual",
  shut_in: "shut-in",
};

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(100 * v).toFixed(1)}%`;
}

function kvol(v: number): string {
  return (v / 1000).toLocaleString(undefined, {
    maximumFractionDigits: 1,
    minimumFractionDigits: 1,
  });
}

function num(v: number | null | undefined, d = 2): string {
  return v === null || v === undefined ? "—" : v.toFixed(d);
}

interface WellGroup {
  api10: string;
  name: string;
  bench: string | null;
  byStream: Partial<Record<PdpStream, PdpRow>>;
  flagged: boolean;
}

export function PdpPage() {
  const dealId = useMapStore((s) => s.pdpDealId);
  const setDealId = useMapStore((s) => s.setPdpDealId);
  const selection = useMapStore((s) => s.pdpSelection);
  const setSelection = useMapStore((s) => s.setPdpSelection);

  const [deals, setDeals] = useState<DealSummary[]>([]);
  const [sources, setSources] = useState<VdrSource[]>([]);
  const [vdrId, setVdrId] = useState<string>("");
  const [includePdnp, setIncludePdnp] = useState(false);
  const [rows, setRows] = useState<PdpRow[]>([]);
  const [series, setSeries] = useState<PdpSeries | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [showExport, setShowExport] = useState(false);

  useEffect(() => {
    listDeals()
      .then(setDeals)
      .catch((e: unknown) => setError(String(e)));
    listVdrSources()
      .then(setSources)
      .catch((e: unknown) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!dealId) return;
    let cancelled = false;
    void (async () => {
      try {
        const cfg = await getPdpConfig(dealId);
        if (!cancelled) {
          setVdrId(cfg?.vdr_id ?? "");
          setIncludePdnp(!!cfg?.include_pdnp);
        }
        const r = await listPdpForecasts(dealId);
        if (!cancelled) setRows(r);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dealId, reload]);

  const selectedRow = useMemo(
    () =>
      selection
        ? (rows.find(
            (r) => r.api10 === selection.api10 && r.stream === selection.stream,
          ) ?? null)
        : null,
    [rows, selection],
  );

  useEffect(() => {
    if (!dealId || !selectedRow) {
      setSeries(null);
      return;
    }
    let cancelled = false;
    fetchPdpSeries(dealId, selectedRow.api10, selectedRow.stream)
      .then((s) => {
        if (!cancelled) setSeries(s);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
    // selectedRow is a fresh object after every reload, so any refit /
    // manual edit / uptime change re-fetches the chart.
  }, [dealId, selectedRow]);

  const wells: WellGroup[] = useMemo(() => {
    const by = new Map<string, WellGroup>();
    for (const r of rows) {
      const g = by.get(r.api10) ?? {
        api10: r.api10,
        name: r.well_name ?? r.api10,
        bench: r.formation_blueox,
        byStream: {},
        flagged: false,
      };
      g.byStream[r.stream] = r;
      if (r.review_flags.some((f) => f !== "at_bound")) g.flagged = true;
      by.set(r.api10, g);
    }
    return [...by.values()].sort(
      (a, b) =>
        Number(b.flagged) - Number(a.flagged) || a.name.localeCompare(b.name),
    );
  }, [rows]);

  const totals = useMemo(() => {
    const t: Record<PdpStream, { cum: number; rem: number }> = {
      oil: { cum: 0, rem: 0 },
      gas: { cum: 0, rem: 0 },
      water: { cum: 0, rem: 0 },
    };
    for (const r of rows) {
      t[r.stream].cum += r.cum_to_date;
      t[r.stream].rem += r.remaining;
    }
    return t;
  }, [rows]);

  const run = useCallback(
    async (label: string, fn: () => Promise<string | null>) => {
      setBusy(label);
      setError(null);
      setMessage(null);
      try {
        const msg = await fn();
        if (msg) setMessage(msg);
        setReload((n) => n + 1);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const saveConfig = () =>
    dealId &&
    void run("saving", async () => {
      // Only the data room — the backend merges, so stored uptime
      // overrides and export settings are kept.
      await putPdpConfig(dealId, { vdr_id: vdrId, include_pdnp: includePdnp });
      return `data room ${vdrId} saved for this deal`;
    });

  const doSync = () =>
    dealId &&
    void run("syncing", async () => {
      const r = await syncPdp(dealId);
      return `synced ${r.wells} wells, ${r.rows.toLocaleString()} days, data through ${r.data_through ?? "—"}`;
    });

  const doForecast = () =>
    dealId &&
    void run("forecasting", async () => {
      const r = await runPdpForecast(dealId);
      return Object.entries(r.counts)
        .map(([k, v]) => `${v} ${k.replace("_", " ")}`)
        .join(", ");
    });

  const patch = (body: PdpPatch, label: string) =>
    dealId &&
    selectedRow &&
    void run(label, async () => {
      await patchPdpForecast(
        dealId,
        selectedRow.api10,
        selectedRow.stream,
        body,
      );
      return null;
    });

  const setUptime = (api10: string, v: number | null) =>
    dealId &&
    void run("uptime", async () => {
      await putWellUptime(dealId, api10, v);
      return v === null ? "uptime override cleared" : `uptime set to ${v}`;
    });

  const firstRow = rows[0] ?? null;

  return (
    <div className="page pdp-page">
      <div className="pdp-toolbar">
        <label className="toolbar-group">
          <span className="toolbar-label">Deal</span>
          <select
            value={dealId ?? ""}
            onChange={(e) => setDealId(e.target.value || null)}
          >
            <option value="">— pick a deal —</option>
            {deals.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
        <label className="toolbar-group">
          <span className="toolbar-label">Data room</span>
          <select
            value={vdrId}
            onChange={(e) => setVdrId(e.target.value)}
            disabled={!dealId}
          >
            <option value="">—</option>
            {sources.map((s) => (
              <option key={s.vdr_id} value={s.vdr_id}>
                {s.vdr_id} ({s.n_wells} wells, through {s.data_through ?? "?"})
              </option>
            ))}
          </select>
        </label>
        <label
          className="toolbar-group"
          title="Seller 2PDNP wells convey: they get a sheet (zero forecast unless you set a restart). Save, then Sync daily."
        >
          <input
            type="checkbox"
            checked={includePdnp}
            disabled={!dealId}
            onChange={(e) => setIncludePdnp(e.target.checked)}
          />{" "}
          Include PDNP
        </label>
        <button
          type="button"
          className="tb-btn"
          disabled={!dealId || !vdrId || !!busy}
          onClick={saveConfig}
        >
          Save
        </button>
        <button
          type="button"
          className="tb-btn"
          disabled={!dealId || !!busy}
          onClick={doSync}
        >
          Sync daily
        </button>
        <button
          type="button"
          className="tb-btn-primary"
          disabled={!dealId || !!busy}
          onClick={doForecast}
        >
          Run forecast
        </button>
        <button
          type="button"
          className="tb-btn"
          disabled={!dealId || rows.length === 0}
          onClick={() => setShowExport((v) => !v)}
        >
          Export to Blue Ox…
        </button>
        {busy && <span className="status-pill status-running">{busy}…</span>}
        {rows.length > 0 && (
          <span className="pdp-totals">
            {STREAMS.map((s) => (
              <span
                key={s}
                title="cum to date / remaining (uptime-applied, to first prod + 50 yr)"
              >
                <span
                  className="swatch"
                  style={{ background: STREAM_COLOR[s] }}
                />{" "}
                {s}: {kvol(totals[s].cum)} /{" "}
                <strong>{kvol(totals[s].rem)}</strong>{" "}
                {s === "gas" ? "MMcf" : "Mbbl"}
              </span>
            ))}
            {firstRow && (
              <span className="muted">
                data through {firstRow.data_through}
              </span>
            )}
          </span>
        )}
      </div>
      {showExport && dealId && (
        <PdpExportPanel dealId={dealId} onClose={() => setShowExport(false)} />
      )}
      {error && <div className="alert alert-error">{error}</div>}
      {message && <div className="alert pdp-message">{message}</div>}

      <div className="pdp-body">
        <div className="pdp-wells review-table-wrap">
          <table className="pdp-table">
            <thead>
              <tr>
                <th>Well</th>
                <th>Bench</th>
                <th title="oil method">Oil</th>
                <th title="oil forward 1-yr effective decline">Fwd eff</th>
                <th title="model / actual, last 90 days (oil)">Tail</th>
                <th title="calendar uptime applied to volumes">Uptime</th>
                <th title="remaining oil, Mbbl">Oil rem</th>
                <th title="remaining gas, MMcf">Gas rem</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {wells.map((w) => {
                const oil = w.byStream.oil;
                const flags = new Set(
                  Object.values(w.byStream).flatMap(
                    (r) => r?.review_flags ?? [],
                  ),
                );
                const active = selection?.api10 === w.api10;
                return (
                  <tr
                    key={w.api10}
                    className={active ? "row-selected" : undefined}
                    onClick={() =>
                      setSelection({
                        api10: w.api10,
                        stream: selection?.stream ?? "oil",
                      })
                    }
                    style={{ cursor: "pointer" }}
                  >
                    <td title={w.api10}>
                      {w.name}
                      {Object.values(w.byStream).some((r) => r?.locked) &&
                        " 🔒"}
                    </td>
                    <td>{w.bench ?? "—"}</td>
                    <td>
                      {oil ? (
                        METHOD_LABEL[oil.method]
                      ) : (
                        <span className="badge badge-err">none</span>
                      )}
                    </td>
                    <td>{pct(oil?.diagnostics.forward_effective_decline)}</td>
                    <td>{num(oil?.tail_ratio)}</td>
                    <td>
                      {num(oil?.uptime_factor, 3)}
                      {oil?.uptime_basis.override !== undefined && " *"}
                    </td>
                    <td>{oil ? kvol(oil.remaining) : "—"}</td>
                    <td>
                      {w.byStream.gas ? kvol(w.byStream.gas.remaining) : "—"}
                    </td>
                    <td>
                      {[...flags]
                        .filter((f) => f !== "at_bound")
                        .map((f) => (
                          <span
                            key={f}
                            className="badge badge-warn"
                            title={FLAG_TEXT[f] ?? f}
                          >
                            {FLAG_SHORT[f] ?? f}
                          </span>
                        ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {dealId && rows.length === 0 && !busy && (
            <p className="muted">
              No PDP forecasts yet — pick the data room, Save, Sync daily, then
              Run forecast.
            </p>
          )}
        </div>

        <div className="pdp-detail">
          {selection && (
            <WellDetail
              key={`${selection.api10}`}
              group={wells.find((w) => w.api10 === selection.api10) ?? null}
              stream={selection.stream}
              row={selectedRow}
              series={series}
              busy={!!busy}
              onStream={(s) =>
                setSelection({ api10: selection.api10, stream: s })
              }
              onPatch={patch}
              onUptime={setUptime}
            />
          )}
          {!selection && (
            <p className="muted">Select a well to review its fit.</p>
          )}
        </div>
      </div>
    </div>
  );
}

interface DetailProps {
  group: WellGroup | null;
  stream: PdpStream;
  row: PdpRow | null;
  series: PdpSeries | null;
  busy: boolean;
  onStream: (s: PdpStream) => void;
  onPatch: (body: PdpPatch, label: string) => void;
  onUptime: (api10: string, v: number | null) => void;
}

function WellDetail({
  group,
  stream,
  row,
  series,
  busy,
  onStream,
  onPatch,
  onUptime,
}: DetailProps) {
  const [fitStart, setFitStart] = useState<string>(row?.fit_start_date ?? "");
  const [qi, setQi] = useState<string>("");
  const [di, setDi] = useState<string>("");
  const [b, setB] = useState<string>("");
  const [anchor, setAnchor] = useState<string>("");
  const [uptime, setUptime] = useState<string>("");

  // Re-seed the editors whenever the persisted row changes.
  useEffect(() => {
    setFitStart(row?.fit_start_date ?? "");
    setQi(row ? row.qi.toFixed(1) : "");
    setDi(row ? row.Di.toFixed(3) : "");
    setB(row ? row.b.toFixed(3) : "");
    setAnchor(row?.anchor_date ?? "");
    setUptime(row ? row.uptime_factor.toFixed(3) : "");
  }, [row]);

  if (!group) return null;
  const manualEff =
    Number.isFinite(parseFloat(di)) && Number.isFinite(parseFloat(b))
      ? effectiveFromNominal(parseFloat(di), parseFloat(b))
      : null;
  const donors = row?.diagnostics.donors;
  const opBreaks = (row?.breaks ?? []).filter(
    (x) => !x.early_life && x.material !== false,
  );
  const locked = !!row?.locked;

  return (
    <div className="pdp-detail-inner">
      <div className="pdp-detail-head">
        <strong>{group.name}</strong>{" "}
        <span className="muted">
          {group.api10} · {group.bench ?? "—"}
        </span>
        <span className="pdp-stream-tabs">
          {STREAMS.map((s) => {
            const r = group.byStream[s];
            return (
              <button
                key={s}
                type="button"
                className={`tb-btn ${s === stream ? "tb-active" : ""}`}
                style={{ borderBottom: `3px solid ${STREAM_COLOR[s]}` }}
                onClick={() => onStream(s)}
                disabled={!r}
                title={r ? undefined : "no forecast for this stream"}
              >
                {s}
                {r ? ` · ${METHOD_LABEL[r.method]}` : ""}
              </button>
            );
          })}
        </span>
      </div>

      {!row && (
        <p className="muted">
          No {stream} forecast for this well (no production, or no donors for a
          transfer).
        </p>
      )}
      {row && series && (
        <PdpChart
          series={series}
          onPickDate={locked ? undefined : (iso) => setFitStart(iso)}
        />
      )}
      {row && (
        <>
          <div className="param-stats pdp-stats">
            <span title="producing-day rate at the anchor">
              qi {row.qi.toFixed(1)}
            </span>
            <span>
              Di {row.Di.toFixed(2)}/yr nominal (
              {pct(row.diagnostics.anchor_effective_decline)} eff. 1-yr from
              anchor)
            </span>
            <span>b {row.b.toFixed(2)}</span>
            <span>Df {(100 * row.Df).toFixed(0)}%</span>
            <span title="1-yr effective decline starting at data_through">
              fwd {pct(row.diagnostics.forward_effective_decline)}
            </span>
            <span>tail {num(row.tail_ratio)}</span>
            <span>anchor {row.anchor_date}</span>
            <span>
              cum {kvol(row.cum_to_date)} · rem{" "}
              <strong>{kvol(row.remaining)}</strong> · EUR {kvol(row.eur)}{" "}
              {stream === "gas" ? "MMcf" : "Mbbl"}
            </span>
            <span>
              uptime {row.uptime_factor.toFixed(3)}
              {row.uptime_basis.override !== undefined
                ? ` (override; computed ${row.uptime_basis.factor.toFixed(3)})`
                : ` (routine downtime; ${row.uptime_basis.event_days} event days excluded)`}
            </span>
            {row.method === "daily_fit" && (
              <span>
                {row.n_points} producing days · R² (log){" "}
                {num(row.fit_r2_log, 3)}
              </span>
            )}
          </div>

          {row.review_flags.length > 0 && (
            <div className="chip-row">
              {row.review_flags.map((f) => (
                <span
                  key={f}
                  className="badge badge-warn"
                  title={FLAG_TEXT[f] ?? f}
                >
                  {FLAG_TEXT[f] ?? f}
                  {f === "at_bound" && row.diagnostics.at_bound
                    ? `: ${row.diagnostics.at_bound}`
                    : ""}
                </span>
              ))}
            </div>
          )}

          {donors && (
            <p className="muted pdp-note">
              Cohort: {donors.n} donors within {donors.radius_mi} mi (
              {Object.entries(donors.by_bench)
                .map(([k, v]) => `${k} ${v}`)
                .join(", ")}
              ; {donors.n_autofit_now} autofit this run) — Di median{" "}
              {donors.di_median.toFixed(2)}, IQR {donors.di_p25.toFixed(2)}–
              {donors.di_p75.toFixed(2)}. Decline starts at {row.data_through}{" "}
              from the trailing-30-day rate.
            </p>
          )}
          {row.method === "manual" && row.diagnostics.previous && (
            <p className="muted pdp-note">
              Manual params replace a {row.diagnostics.previous.method} (Di{" "}
              {row.diagnostics.previous.params.Di?.toFixed(2)}, b{" "}
              {row.diagnostics.previous.params.b?.toFixed(2)}).
            </p>
          )}
          {opBreaks.length > 0 && (
            <ul className="pdp-breaks">
              {opBreaks.map((x) => (
                <li key={`${x.kind}${x.start}`}>
                  {x.kind === "shut_in"
                    ? `Shut-in ${x.start} → ${x.end} (${x.days} d)`
                    : `Choke ${x.choke_from} → ${x.choke_to} on ${x.start}`}
                  {x.rate_ratio
                    ? ` — oil ${x.oil_rate_before} → ${x.oil_rate_after} bopd (×${x.rate_ratio.toFixed(2)})`
                    : ""}
                  {!locked && (
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() =>
                        setFitStart(
                          x.kind === "shut_in" && x.end ? x.end : x.start,
                        )
                      }
                    >
                      use as fit start
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          <div className="pdp-controls">
            <fieldset disabled={busy || locked}>
              <legend>Fit window</legend>
              <input
                type="date"
                value={fitStart}
                onChange={(e) => setFitStart(e.target.value)}
              />
              <button
                type="button"
                className="tb-btn"
                disabled={!fitStart}
                onClick={() =>
                  onPatch({ fit_start_date: fitStart }, "refitting")
                }
              >
                Refit from date
              </button>
              <button
                type="button"
                className="tb-btn"
                disabled={
                  row.method === "transfer_now" ||
                  (!row.fit_start_date && row.method !== "manual")
                }
                onClick={() => onPatch({ clear_fit_start: true }, "reverting")}
                title="Clear the window and any manual params — back to the auto method"
              >
                Revert to auto
              </button>
              <span className="muted">
                Click the chart to pick a date. Time stays measured from the
                peak.
              </span>
            </fieldset>
            <fieldset disabled={busy || locked}>
              <legend>Manual parameters</legend>
              <label>
                qi{" "}
                <input
                  className="tweak-input"
                  value={qi}
                  onChange={(e) => setQi(e.target.value)}
                />
              </label>
              <label>
                Di/yr nominal{" "}
                <input
                  className="tweak-input"
                  value={di}
                  onChange={(e) => setDi(e.target.value)}
                />
              </label>
              <span className="muted">= {pct(manualEff)} eff.</span>
              <label>
                b{" "}
                <input
                  className="tweak-input"
                  value={b}
                  onChange={(e) => setB(e.target.value)}
                />
              </label>
              <label>
                anchor{" "}
                <input
                  type="date"
                  value={anchor}
                  onChange={(e) => setAnchor(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="tb-btn"
                disabled={
                  ![qi, di, b].every((v) => Number.isFinite(parseFloat(v))) ||
                  !anchor
                }
                onClick={() =>
                  onPatch(
                    {
                      params: {
                        qi: parseFloat(qi),
                        Di: parseFloat(di),
                        b: parseFloat(b),
                        anchor_date: anchor,
                      },
                    },
                    "saving params",
                  )
                }
              >
                Apply
              </button>
            </fieldset>
            <fieldset disabled={busy}>
              <legend>Well uptime (all streams)</legend>
              <input
                className="tweak-input"
                value={uptime}
                onChange={(e) => setUptime(e.target.value)}
              />
              <button
                type="button"
                className="tb-btn"
                disabled={!(parseFloat(uptime) > 0 && parseFloat(uptime) <= 1)}
                onClick={() => onUptime(group.api10, parseFloat(uptime))}
              >
                Override
              </button>
              <button
                type="button"
                className="tb-btn"
                disabled={row.uptime_basis.override === undefined}
                onClick={() => onUptime(group.api10, null)}
              >
                Use computed
              </button>
            </fieldset>
            <fieldset disabled={busy}>
              <legend>Sign-off</legend>
              <button
                type="button"
                className={locked ? "tb-btn tb-active" : "tb-btn"}
                onClick={() =>
                  onPatch({ locked: !locked }, locked ? "unlocking" : "locking")
                }
              >
                {locked ? "🔒 Locked — unlock" : "Lock this stream"}
              </button>
            </fieldset>
          </div>
        </>
      )}
    </div>
  );
}
