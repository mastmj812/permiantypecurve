// One stream's review block inside the well-level PDP review (oil, gas
// and water stack on one screen — no stream switching). Chart + readout
// + flags + compact edit row; clicking the chart picks the fit-window
// start for THIS stream. Di is shown nominal with the 1-yr effective.

import { useEffect, useState } from "react";

import {
  PDP_FLAG_TEXT as FLAG_TEXT,
  PDP_METHOD_LABEL as METHOD_LABEL,
  STREAM_COLOR,
  effectiveFromNominal,
  type PdpPatch,
  type PdpRow,
  type PdpSeries,
  type PdpStream,
} from "../api/pdp";
import { PdpChart } from "./PdpChart";

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(100 * v).toFixed(1)}%`;
}

function kvol(v: number): string {
  return (v / 1000).toLocaleString(undefined, {
    maximumFractionDigits: 1,
    minimumFractionDigits: 1,
  });
}

interface Props {
  row: PdpRow;
  series: PdpSeries | null;
  busy: boolean;
  chartWidth: number;
  onPatch: (stream: PdpStream, body: PdpPatch, label: string) => void;
}

export function PdpStreamPanel({
  row,
  series,
  busy,
  chartWidth,
  onPatch,
}: Props) {
  const stream = row.stream;
  const locked = row.locked;
  const [fitStart, setFitStart] = useState(row.fit_start_date ?? "");
  const [qi, setQi] = useState(row.qi.toFixed(1));
  const [di, setDi] = useState(row.Di.toFixed(3));
  const [b, setB] = useState(row.b.toFixed(3));
  const [anchor, setAnchor] = useState(row.anchor_date);

  useEffect(() => {
    setFitStart(row.fit_start_date ?? "");
    setQi(row.qi.toFixed(1));
    setDi(row.Di.toFixed(3));
    setB(row.b.toFixed(3));
    setAnchor(row.anchor_date);
  }, [row]);

  const manualEff =
    Number.isFinite(parseFloat(di)) && Number.isFinite(parseFloat(b))
      ? effectiveFromNominal(parseFloat(di), parseFloat(b))
      : null;
  const flags = row.review_flags.filter((f) => f !== "at_bound");
  const breaks = row.breaks.filter(
    (x) => !x.early_life && x.material !== false,
  );
  const donors = row.diagnostics.donors;
  const unit = stream === "gas" ? "MMcf" : "Mbbl";

  return (
    <section className={`pdp-stream ${locked ? "pdp-stream-locked" : ""}`}>
      <header className="pdp-stream-head">
        <span className="swatch" style={{ background: STREAM_COLOR[stream] }} />
        <strong>{stream}</strong>
        <span className="badge badge-muted">
          {METHOD_LABEL[row.method] ?? row.method}
        </span>
        <span className="pdp-stream-stats">
          Di {row.Di.toFixed(2)}/yr (
          {pct(row.diagnostics.anchor_effective_decline)} eff) · b{" "}
          {row.b.toFixed(2)} · fwd{" "}
          {pct(row.diagnostics.forward_effective_decline)} · tail{" "}
          {row.tail_ratio?.toFixed(2) ?? "—"} · rem{" "}
          <strong>{kvol(row.remaining)}</strong> {unit} · EUR {kvol(row.eur)}
        </span>
        <button
          type="button"
          className={locked ? "tb-btn tb-active" : "tb-btn"}
          disabled={busy}
          onClick={() =>
            onPatch(
              stream,
              { locked: !locked },
              locked ? "unlocking" : "locking",
            )
          }
        >
          {locked ? "🔒 locked" : "lock"}
        </button>
      </header>

      {(flags.length > 0 || row.diagnostics.at_bound) && (
        <div className="chip-row">
          {flags.map((f) => (
            <span
              key={f}
              className="badge badge-warn"
              title={FLAG_TEXT[f] ?? f}
            >
              {FLAG_TEXT[f] ?? f}
            </span>
          ))}
          {row.diagnostics.at_bound && (
            <span className="badge badge-muted">
              at bound: {row.diagnostics.at_bound}
            </span>
          )}
        </div>
      )}

      {series ? (
        <PdpChart
          series={series}
          width={chartWidth}
          height={210}
          onPickDate={locked ? undefined : (iso) => setFitStart(iso)}
        />
      ) : (
        <div className="pdp-chart-placeholder muted">loading chart…</div>
      )}

      {donors && (
        <p className="muted pdp-note">
          Cohort {donors.n} donors / {donors.radius_mi} mi (
          {Object.entries(donors.by_bench)
            .map(([k, v]) => `${k} ${v}`)
            .join(", ")}
          ), Di median {donors.di_median.toFixed(2)} (IQR{" "}
          {donors.di_p25.toFixed(2)}–{donors.di_p75.toFixed(2)}), decline from{" "}
          {row.data_through}.
        </p>
      )}
      {breaks.length > 0 && (
        <details className="pdp-note">
          <summary>{breaks.length} operational break(s)</summary>
          <ul className="pdp-breaks">
            {breaks.map((x) => (
              <li key={`${x.kind}${x.start}`}>
                {x.kind === "shut_in"
                  ? `Shut-in ${x.start} → ${x.end} (${x.days} d)`
                  : `Choke ${x.choke_from} → ${x.choke_to} on ${x.start}`}
                {x.rate_ratio ? ` — oil ×${x.rate_ratio.toFixed(2)}` : ""}
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
        </details>
      )}

      <fieldset className="pdp-stream-edit" disabled={busy || locked}>
        <input
          type="date"
          value={fitStart}
          onChange={(e) => setFitStart(e.target.value)}
          title="fit-window start"
        />
        <button
          type="button"
          className="tb-btn"
          disabled={!fitStart}
          onClick={() =>
            onPatch(stream, { fit_start_date: fitStart }, "refitting")
          }
        >
          Refit from date
        </button>
        <button
          type="button"
          className="tb-btn"
          disabled={
            row.method === "transfer_now" ||
            row.method === "shut_in" ||
            (!row.fit_start_date && row.method !== "manual")
          }
          onClick={() =>
            onPatch(stream, { clear_fit_start: true }, "reverting")
          }
          title="Clear the window / manual params — back to the auto method"
        >
          Revert to auto
        </button>
        <details className="pdp-manual">
          <summary>Manual…</summary>
          <label>
            qi{" "}
            <input
              className="tweak-input"
              value={qi}
              onChange={(e) => setQi(e.target.value)}
            />
          </label>
          <label>
            Di/yr{" "}
            <input
              className="tweak-input"
              value={di}
              onChange={(e) => setDi(e.target.value)}
            />
          </label>
          <span className="muted">= {pct(manualEff)} eff</span>
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
                stream,
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
        </details>
      </fieldset>
    </section>
  );
}
