import { legendByBasin, BASINS, type BasinBlueox } from "../map/formations";

const BASIN_LABEL: Record<BasinBlueox, string> = {
  delaware: "Delaware",
  midland: "Midland",
  cbp: "Central Basin Platform",
};

export function Legend() {
  const byBasin = legendByBasin();
  const basins = BASINS;
  return (
    <div className="legend">
      <header className="legend-header">Legend</header>
      <div className="legend-section">
        <strong>Formation (Blue Ox)</strong>
        {basins.map((basin) => (
          <div key={basin} className="legend-group">
            <span className="muted">{BASIN_LABEL[basin]}</span>
            {byBasin[basin].map((c) => (
              <span key={c.code} className="legend-item">
                <span className="swatch" style={{ background: c.color }} />
                {c.code}
              </span>
            ))}
          </div>
        ))}
      </div>
      <div className="legend-section">
        <strong>Wellstick</strong>
        <div className="legend-item">
          <span className="wellstick-sample solid" /> lateral path
        </div>
      </div>
    </div>
  );
}
