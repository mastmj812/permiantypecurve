// PDP review tab: forecasts fit on seller data-room DAILY production
// (backend app/forecasting/daily.py), deal-scoped in pdp_forecasts —
// never the global forecasts table.
//
// Flow: pick deal -> pick data room (saved to deals.pdp_config) -> Sync
// (copy the room's daily rows) -> Run forecast -> review WELL BY WELL:
// the queue (left) orders shut-in, then flagged, then clean wells; the
// review pane (right) shows oil, gas and water together. "Accept & next"
// (A) locks every stream of the well and jumps to the next unsigned
// well; N / P step without locking. "Accept clean wells" bulk-locks every
// unsigned well with no flag beyond at_bound. Manual params, windows and
// locks survive every re-run.
//
// Conventions on screen: Di is NOMINAL per year with the 1-yr effective
// alongside (anchor = from the params' t = 0; fwd = the year after
// data_through). Rates are producing-day; volumes are calendar (x uptime).
// Remaining = data_through -> first prod + 50 yr (raw technical, no
// economic limit).

import {
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { listDeals, type DealSummary } from "../api/deals";
import {
  PDP_FLAG_SHORT,
  PDP_FLAG_TEXT,
  STREAM_COLOR,
  fetchPdpSeries,
  getPdpConfig,
  listPdpForecasts,
  listVdrSources,
  lockWells,
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
} from "../api/pdp";
import { PdpExportPanel } from "../components/PdpExportPanel";
import { PdpStreamPanel } from "../components/PdpStreamPanel";
import {
  cleanUnsigned,
  groupWells,
  nextUnsigned,
  orderQueue,
  stepQueue,
  wellFlags,
  wellStatus,
  type QueueWell,
} from "../pdp/reviewQueue";
import { useMapStore } from "../store/mapStore";

const STREAMS: PdpStream[] = ["oil", "gas", "water"];

function kvol(v: number): string {
  return (v / 1000).toLocaleString(undefined, {
    maximumFractionDigits: 1,
    minimumFractionDigits: 1,
  });
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    el.isContentEditable
  );
}

const STATUS_MARK: Record<string, string> = {
  signed: "✓",
  partial: "◐",
  open: "",
};

export function PdpPage() {
  const dealId = useMapStore((s) => s.pdpDealId);
  const setDealId = useMapStore((s) => s.setPdpDealId);
  const selection = useMapStore((s) => s.pdpSelection);
  const setSelection = useMapStore((s) => s.setPdpSelection);
  const current = selection?.api10 ?? null;
  const select = useCallback(
    (api10: string | null) =>
      setSelection(api10 ? { api10, stream: "oil" } : null),
    [setSelection],
  );

  const [deals, setDeals] = useState<DealSummary[]>([]);
  const [sources, setSources] = useState<VdrSource[]>([]);
  const [vdrId, setVdrId] = useState<string>("");
  const [includePdnp, setIncludePdnp] = useState(false);
  const [rows, setRows] = useState<PdpRow[]>([]);
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

  const queue = useMemo(() => orderQueue(groupWells(rows)), [rows]);
  const signed = queue.filter((w) => wellStatus(w) === "signed").length;
  const clean = useMemo(() => cleanUnsigned(queue), [queue]);
  const currentWell = queue.find((w) => w.api10 === current) ?? null;

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

  const patch = (stream: PdpStream, body: PdpPatch, label: string) =>
    dealId &&
    current &&
    void run(label, async () => {
      await patchPdpForecast(dealId, current, stream, body);
      return null;
    });

  const setUptime = (api10: string, v: number | null) =>
    dealId &&
    void run("uptime", async () => {
      await putWellUptime(dealId, api10, v);
      return v === null ? "uptime override cleared" : `uptime set to ${v}`;
    });

  const accept = useCallback(() => {
    if (!dealId || !current || busy) return;
    const next = nextUnsigned(queue, current);
    void run("accepting", async () => {
      await lockWells(dealId, [current]);
      select(next);
      return next ? null : "every well is signed off";
    });
  }, [dealId, current, busy, queue, run, select]);

  const unlockWell = () =>
    dealId &&
    current &&
    void run("unlocking", async () => {
      await lockWells(dealId, [current], false);
      return null;
    });

  const acceptClean = () => {
    if (!dealId || clean.length === 0) return;
    const names = clean.map((w) => `  • ${w.name}`).join("\n");
    if (
      !window.confirm(
        `Lock every stream of these ${clean.length} clean wells?\n\n${names}`,
      )
    )
      return;
    void run("accepting clean wells", async () => {
      const r = await lockWells(
        dealId,
        clean.map((w) => w.api10),
      );
      return `signed off ${r.wells} clean wells (${r.changed} streams locked)`;
    });
  };

  // Keyboard: A accept & next, N next, P previous (ignored while typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === "a") {
        e.preventDefault();
        accept();
      } else if (k === "n" || k === "p") {
        e.preventDefault();
        select(stepQueue(queue, current, k === "n" ? 1 : -1));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [accept, queue, current, select]);

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
        <div className="pdp-wells">
          {queue.length > 0 && (
            <div className="pdp-queue-head">
              <div
                className="pdp-progress"
                title="wells with every stream locked"
              >
                <div
                  className="pdp-progress-bar"
                  style={{ width: `${(100 * signed) / queue.length}%` }}
                />
                <span>
                  {signed} of {queue.length} wells signed off
                </span>
              </div>
              <button
                type="button"
                className="tb-btn"
                disabled={!!busy || clean.length === 0}
                onClick={acceptClean}
                title="Lock every stream of each unsigned well with no flag beyond at-bound"
              >
                Accept clean wells ({clean.length})
              </button>
            </div>
          )}
          <table className="pdp-table">
            <thead>
              <tr>
                <th />
                <th>Well</th>
                <th>Bench</th>
                <th title="remaining oil, Mbbl">Oil rem</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {queue.map((w) => {
                const st = wellStatus(w);
                return (
                  <tr
                    key={w.api10}
                    className={[
                      w.api10 === current ? "row-selected" : "",
                      st === "signed" ? "row-signed" : "",
                    ].join(" ")}
                    onClick={() => select(w.api10)}
                    style={{ cursor: "pointer" }}
                  >
                    <td className="pdp-status" title={st}>
                      {STATUS_MARK[st]}
                    </td>
                    <td title={w.api10}>{w.name}</td>
                    <td>{w.bench ?? "—"}</td>
                    <td>
                      {w.byStream.oil ? kvol(w.byStream.oil.remaining) : "—"}
                    </td>
                    <td>
                      {wellFlags(w).map((f) => (
                        <span
                          key={f}
                          className="badge badge-warn"
                          title={PDP_FLAG_TEXT[f] ?? f}
                        >
                          {PDP_FLAG_SHORT[f] ?? f}
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
          {currentWell && dealId ? (
            <WellReview
              dealId={dealId}
              well={currentWell}
              busy={!!busy}
              position={
                queue.findIndex((w) => w.api10 === currentWell.api10) + 1
              }
              total={queue.length}
              onAccept={accept}
              onStep={(step) => select(stepQueue(queue, current, step))}
              onUnlock={unlockWell}
              onPatch={patch}
              onUptime={setUptime}
            />
          ) : (
            <p className="muted">
              {queue.length > 0 ? (
                <>
                  Select a well, or press <kbd>N</kbd> to start at the top of
                  the queue.
                </>
              ) : (
                "Select a deal to review its PDP forecasts."
              )}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function useElementWidth<T extends HTMLElement>(): [RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(760);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(480, Math.floor(entry.contentRect.width)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

interface ReviewProps {
  dealId: string;
  well: QueueWell;
  busy: boolean;
  position: number;
  total: number;
  onAccept: () => void;
  onStep: (step: 1 | -1) => void;
  onUnlock: () => void;
  onPatch: (stream: PdpStream, body: PdpPatch, label: string) => void;
  onUptime: (api10: string, v: number | null) => void;
}

function WellReview({
  dealId,
  well,
  busy,
  position,
  total,
  onAccept,
  onStep,
  onUnlock,
  onPatch,
  onUptime,
}: ReviewProps) {
  const [series, setSeries] = useState<Partial<Record<PdpStream, PdpSeries>>>(
    {},
  );
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const streams = STREAMS.filter((s) => well.byStream[s]);
  const status = wellStatus(well);
  const first = streams
    .map((s) => well.byStream[s])
    .find((r): r is PdpRow => r !== undefined);
  const [uptime, setUptime] = useState(
    first ? first.uptime_factor.toFixed(3) : "",
  );

  // Re-fetch the charts only when a fit actually changes (not on lock
  // toggles or unrelated reloads): the key covers every series input.
  const fetchKey = JSON.stringify([
    well.api10,
    streams.map((s) => {
      const r = well.byStream[s];
      return r
        ? [
            s,
            r.method,
            r.qi,
            r.Di,
            r.b,
            r.anchor_date,
            r.fit_start_date,
            r.uptime_factor,
          ]
        : [s];
    }),
  ]);
  useEffect(() => {
    let cancelled = false;
    const [api10, keyed] = JSON.parse(fetchKey) as [
      string,
      Array<[PdpStream, ...unknown[]]>,
    ];
    setSeries({});
    void Promise.all(
      keyed.map(
        async ([s]) => [s, await fetchPdpSeries(dealId, api10, s)] as const,
      ),
    )
      .then((pairs) => {
        if (!cancelled) setSeries(Object.fromEntries(pairs));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [dealId, fetchKey]);

  const uptimeNow = first?.uptime_factor;
  useEffect(() => {
    if (uptimeNow !== undefined) setUptime(uptimeNow.toFixed(3));
  }, [uptimeNow]);

  const missing = STREAMS.filter((s) => !well.byStream[s]);

  return (
    <div className="pdp-review" ref={ref}>
      <div className="pdp-review-head">
        <div>
          <strong>{well.name}</strong>{" "}
          <span className="muted">
            {well.api10} · {well.bench ?? "—"} · {position} of {total}
          </span>{" "}
          {status === "signed" && (
            <span className="badge badge-ok">signed off</span>
          )}
          {status === "partial" && (
            <span className="badge badge-info">partly locked</span>
          )}
        </div>
        <div className="pdp-review-actions">
          <button
            type="button"
            className="tb-btn"
            onClick={() => onStep(-1)}
            title="previous well (P)"
          >
            ◀ Prev <kbd>P</kbd>
          </button>
          <button
            type="button"
            className="tb-btn"
            onClick={() => onStep(1)}
            title="next well, no lock (N)"
          >
            Next <kbd>N</kbd> ▶
          </button>
          {status !== "open" && (
            <button
              type="button"
              className="tb-btn"
              disabled={busy}
              onClick={onUnlock}
            >
              Unlock well
            </button>
          )}
          <button
            type="button"
            className="tb-btn-primary"
            disabled={busy || status === "signed"}
            onClick={onAccept}
            title="lock oil, gas and water as they stand and go to the next unsigned well (A)"
          >
            Accept &amp; next <kbd>A</kbd>
          </button>
        </div>
      </div>
      {first && (
        <div className="pdp-uptime-row">
          <span className="muted">
            Well uptime {first.uptime_factor.toFixed(3)}
            {first.uptime_basis.override !== undefined
              ? ` (override; computed ${first.uptime_basis.factor.toFixed(3)})`
              : ` (routine downtime; ${first.uptime_basis.event_days} event days excluded)`}{" "}
            · data through {first.data_through}
          </span>
          <input
            className="tweak-input"
            value={uptime}
            onChange={(e) => setUptime(e.target.value)}
            disabled={busy}
          />
          <button
            type="button"
            className="tb-btn"
            disabled={
              busy || !(parseFloat(uptime) > 0 && parseFloat(uptime) <= 1)
            }
            onClick={() => onUptime(well.api10, parseFloat(uptime))}
          >
            Override
          </button>
          <button
            type="button"
            className="tb-btn"
            disabled={busy || first.uptime_basis.override === undefined}
            onClick={() => onUptime(well.api10, null)}
          >
            Use computed
          </button>
        </div>
      )}
      {streams.map((s) => {
        const row = well.byStream[s];
        return row ? (
          <PdpStreamPanel
            key={s}
            row={row}
            series={series[s] ?? null}
            busy={busy}
            chartWidth={Math.min(width - 4, 1100)}
            onPatch={onPatch}
          />
        ) : null;
      })}
      {missing.length > 0 && (
        <p className="muted">
          No {missing.join(" / ")} forecast for this well (no production on that
          stream).
        </p>
      )}
    </div>
  );
}
