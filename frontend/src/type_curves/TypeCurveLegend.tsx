// Legend for the Type Curve chart. Mirrors panels/Legend.tsx in structure
// + styles. Each line/band on the chart gets a row here so it's obvious
// what's "the type curve" vs. context vs. the raw signal.

interface Props {
  showCompare?: boolean;
  compareName?: string | null;
}

export function TypeCurveLegend({ showCompare = false, compareName }: Props) {
  return (
    <div className="legend type-curve-legend">
      <header className="legend-header">Legend</header>

      <div className="legend-section">
        <div className="legend-item">
          <span className="line-sample" style={{ borderTopColor: "#0f172a", borderTopWidth: 2.5 }} />
          <strong>Fitted type curve</strong>
          <span className="muted"> &nbsp;Arps fit to P50 (the published curve)</span>
        </div>
        <div className="legend-item">
          <span
            className="line-sample"
            style={{ borderTopColor: "#1d4ed8", borderTopWidth: 2.25 }}
          />
          <strong>P50 (raw monthly median)</strong>
          <span className="muted"> &nbsp;visual fit target</span>
        </div>
        <div className="legend-item">
          <span
            className="line-sample"
            style={{ borderTopColor: "#9ca3af", borderTopStyle: "dashed" }}
          />
          Mean <span className="muted">— biased high by outliers; not a good visual fit target</span>
        </div>
      </div>

      <div className="legend-section">
        <strong>Distribution bands</strong>
        <div className="legend-item">
          <span className="band-sample" style={{ background: "#60a5fa", opacity: 0.45 }} />
          P25 – P75 (interquartile)
        </div>
        <div className="legend-item">
          <span className="band-sample" style={{ background: "#bfdbfe", opacity: 0.55 }} />
          P10 – P90 (80% band; P10 = high case, SPE)
        </div>
      </div>

      <div className="legend-section">
        <div className="legend-item">
          <span className="line-sample" style={{ borderTopColor: "#6b7280" }} />
          Well count per month (bottom strip)
        </div>
      </div>

      {showCompare && compareName && (
        <div className="legend-section">
          <strong>Comparison</strong>
          <div className="legend-item">
            <span className="line-sample" style={{ borderTopColor: "#16a34a", borderTopWidth: 2 }} />
            P50 of {compareName}
          </div>
        </div>
      )}
    </div>
  );
}
