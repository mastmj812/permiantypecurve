import { useMapStore } from "../store/mapStore";

const SOFT_CAP = 300;
const HARD_CAP = 500;

export function SummaryDrawer() {
  const summary = useMapStore((s) => s.summary);
  const selected = useMapStore((s) => s.selectedApi14s);
  const clearSelection = useMapStore((s) => s.clearSelection);

  const count = summary?.count ?? selected.size;

  if (count === 0) {
    return (
      <aside className="panel panel-right">
        <header className="panel-header">
          <span>Selection</span>
        </header>
        <p className="muted">
          Use the lasso, box, or click tools to select wells. The cap is{" "}
          <strong>{HARD_CAP}</strong>; warnings appear at <strong>{SOFT_CAP}</strong>.
        </p>
      </aside>
    );
  }

  const overHard = summary?.exceeds_hard_cap ?? count > HARD_CAP;
  const overSoft = summary?.exceeds_soft_cap ?? count > SOFT_CAP;

  return (
    <aside className="panel panel-right">
      <header className="panel-header">
        <span>Selection ({count.toLocaleString()})</span>
        <button className="link-btn" type="button" onClick={clearSelection}>
          clear
        </button>
      </header>

      {overHard && (
        <div className="alert alert-error">
          Over the {HARD_CAP}-well hard cap — reduce the selection before forecasting.
        </div>
      )}
      {!overHard && overSoft && (
        <div className="alert alert-warn">
          Above {SOFT_CAP} wells; forecasting may slow down.
        </div>
      )}

      {summary && (
        <>
          <Stat
            label="Median lateral"
            value={
              summary.median_lateral_ft != null
                ? `${Math.round(summary.median_lateral_ft).toLocaleString()} ft`
                : "—"
            }
          />

          <section className="filter-section">
            <h3>Vintage</h3>
            <VintageHistogram histogram={summary.vintage_histogram} />
          </section>

          <section className="filter-section">
            <h3>Top operators</h3>
            <ol className="op-list">
              {summary.operators_top5.map(([op, n]) => (
                <li key={op}>
                  {op} <span className="muted">({n})</span>
                </li>
              ))}
            </ol>
          </section>

          <section className="filter-section">
            <h3>Formations</h3>
            <ul className="op-list">
              {Object.entries(summary.formations).map(([f, n]) => (
                <li key={f}>
                  {f} <span className="muted">({n})</span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}

      <button
        type="button"
        className="btn-primary"
        disabled={overHard || count === 0}
        title={overHard ? "Reduce selection below the hard cap" : "Run auto-forecast"}
        onClick={() => {
          const api14s = Array.from(useMapStore.getState().selectedApi14s);
          useMapStore.getState().setForecastApi14s(api14s);
          useMapStore.getState().setCurrentPage("forecast");
        }}
      >
        Forecast {count} well{count === 1 ? "" : "s"}
      </button>
    </aside>
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

function VintageHistogram({ histogram }: { histogram: Record<number, number> }) {
  const entries = Object.entries(histogram)
    .map(([y, n]) => [Number(y), n] as [number, number])
    .sort(([a], [b]) => a - b);
  if (!entries.length) return <p className="muted">—</p>;
  const max = Math.max(...entries.map(([, n]) => n));
  return (
    <div className="histo">
      {entries.map(([year, n]) => (
        <div key={year} className="histo-row">
          <span className="histo-label">{year}</span>
          <span className="histo-bar" style={{ width: `${(n / max) * 100}%` }} />
          <span className="histo-count">{n}</span>
        </div>
      ))}
    </div>
  );
}
