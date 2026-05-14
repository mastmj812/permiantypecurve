import { groupedFormations } from "../map/formations";

export function Legend() {
  const groups = groupedFormations();
  return (
    <div className="legend">
      <header className="legend-header">Legend</header>
      <div className="legend-section">
        <strong>Formation</strong>
        {(["Wolfcamp", "Bone Spring", "Spraberry"] as const).map((g) => (
          <div key={g} className="legend-group">
            <span className="muted">{g}</span>
            {groups[g].map((f) => (
              <span key={f.name} className="legend-item">
                <span className="swatch" style={{ background: f.color }} />
                {f.name}
              </span>
            ))}
          </div>
        ))}
      </div>
      <div className="legend-section">
        <strong>Wellstick</strong>
        <div className="legend-item">
          <span className="wellstick-sample solid" /> heel → bottomhole (survey)
        </div>
        <div className="legend-item">
          <span className="wellstick-sample dashed" /> surface → bottomhole (no survey)
        </div>
      </div>
    </div>
  );
}
