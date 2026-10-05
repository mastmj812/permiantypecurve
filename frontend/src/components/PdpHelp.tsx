// In-app instructions for the PDP tab: setup, the well-by-well review
// loop, sending to Blue Ox, how to read the numbers, and the flag legend.
// Toggled by the toolbar "How it works" button or the ? key.

import { PDP_FLAG_TEXT, PDP_FLAG_SHORT } from "../api/pdp";

const KEYS: Array<[string, string]> = [
  [
    "A",
    "Accept & next — lock oil, gas and water as they stand, jump to the next unsigned well",
  ],
  ["N", "Next well in the queue (no lock)"],
  ["P", "Previous well in the queue (no lock)"],
  ["?", "Show / hide these instructions"],
];

export function PdpHelp({ onClose }: { onClose: () => void }) {
  return (
    <div className="pdp-help">
      <div className="pdp-help-head">
        <strong>How the PDP tab works</strong>
        <span className="muted">
          Every change saves the moment you click — there is no save step for
          forecasts.
        </span>
        <button type="button" className="link-btn" onClick={onClose}>
          close
        </button>
      </div>

      <div className="pdp-help-grid">
        <section>
          <h4>1 · Set up a deal (once)</h4>
          <ol>
            <li>
              Pick the <b>Deal</b> and its <b>Data room</b> (the seller's VDR
              production package).
            </li>
            <li>
              Tick <b>Include PDNP</b> if the seller's non-producing wells
              convey. Shut-in wells forecast zero unless you set a restart.
            </li>
            <li>
              <b>Save</b>, then <b>Sync daily</b> (copies the seller's daily
              volumes; repeat after a new production update is loaded).
            </li>
            <li>
              <b>Run forecast</b> fits every stream. Re-running later keeps your
              manual parameters, fit windows and locks.
            </li>
          </ol>
        </section>

        <section>
          <h4>2 · Review well by well</h4>
          <ol>
            <li>
              The queue lists <b>shut-in</b> wells first, then <b>flagged</b>,
              then <b>clean</b>; signed-off wells (✓) drop to the bottom, partly
              locked ones show ◐. Press <kbd>N</kbd> to start.
            </li>
            <li>
              Oil, gas and water are stacked for the selected well. To adjust a
              stream:
              <ul>
                <li>
                  <b>Fit window</b> — click the chart (or{" "}
                  <i>use as fit start</i> on a break), then{" "}
                  <b>Refit from date</b>. Time stays measured from the peak, so
                  Di keeps its meaning.
                </li>
                <li>
                  <b>Manual…</b> — set qi, Di, b and the anchor date. An anchor
                  in the future is a restart (zero until then).
                </li>
                <li>
                  <b>Revert to auto</b> clears a window or manual parameters.
                </li>
                <li>
                  <b>Well uptime</b> (above the streams) overrides the factor
                  for all three streams.
                </li>
              </ul>
            </li>
            <li>
              Press <kbd>A</kbd> to accept the well and move on. <kbd>N</kbd> /{" "}
              <kbd>P</kbd> step without locking.
            </li>
            <li>
              <b>Accept clean wells</b> signs off every unsigned well with no
              flag beyond at-bound in one go (it lists them first).
            </li>
            <li>
              To change a signed-off well, open it and click <b>Unlock well</b>{" "}
              (or a stream's lock button).
            </li>
          </ol>
        </section>

        <section>
          <h4>3 · Send to Blue Ox</h4>
          <ol>
            <li>
              Get the deal effective date and grouping preference from Blue Ox.
            </li>
            <li>Sign off every well (progress bar reads “22 of 22”).</li>
            <li>
              <b>Export to Blue Ox…</b> → effective date, grouping (one sheet
              per well is the default), <i>Supersedes</i> only on a re-send →{" "}
              <b>Save + preview</b>.
            </li>
            <li>
              Clear any red contract error; aim for zero <i>unlocked</i>{" "}
              warnings → <b>Download</b>.
            </li>
            <li>Email the file yourself — the app never sends anything.</li>
          </ol>
        </section>

        <section>
          <h4>Reading the numbers</h4>
          <ul>
            <li>
              <b>Di</b> is nominal per year; the % beside it is the 1-yr
              effective decline from the anchor. <b>fwd</b> is the effective
              decline over the year after the last data day.
            </li>
            <li>
              Rates on the charts are <b>producing-day</b>; volumes are{" "}
              <b>calendar</b> (rate × uptime).
            </li>
            <li>
              <b>rem</b> = last data day → first production + 50 yr (raw
              technical, no economic limit). <b>EUR</b> = reported cum + rem.
            </li>
            <li>
              <b>tail</b> = model ÷ actual over the last 90 days; flagged
              outside 0.85–1.15.
            </li>
            <li>
              Chart: solid = fit, dashed = forecast, grey band = shut-in, purple
              line = material choke change (faint = early-life ramp), amber line
              = your fit-window start.
            </li>
          </ul>
        </section>

        <section>
          <h4>Flags</h4>
          <ul>
            {Object.entries(PDP_FLAG_SHORT).map(([k, short]) => (
              <li key={k}>
                <span className="badge badge-warn">{short}</span>{" "}
                {PDP_FLAG_TEXT[k] ?? k}
              </li>
            ))}
            <li>
              <span className="badge badge-muted">at bound</span>{" "}
              {PDP_FLAG_TEXT.at_bound} (informational; doesn't block “clean”)
            </li>
          </ul>
        </section>

        <section>
          <h4>Keys</h4>
          <table className="pdp-help-keys">
            <tbody>
              {KEYS.map(([k, what]) => (
                <tr key={k}>
                  <td>
                    <kbd>{k}</kbd>
                  </td>
                  <td>{what}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">
            Keys are ignored while you're typing in a field.
          </p>
        </section>
      </div>
    </div>
  );
}
