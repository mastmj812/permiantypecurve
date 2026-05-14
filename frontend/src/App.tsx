import { HealthBadge } from "./components/HealthBadge";
import { ForecastPage } from "./pages/ForecastPage";
import { MapPage } from "./pages/MapPage";
import { useMapStore } from "./store/mapStore";

export function App() {
  const page = useMapStore((s) => s.currentPage);
  const setPage = useMapStore((s) => s.setCurrentPage);

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title">Permian Type Curve</span>
        <nav className="app-nav">
          <button
            type="button"
            className={`nav-tab ${page === "map" ? "nav-active" : ""}`}
            onClick={() => setPage("map")}
          >
            Map
          </button>
          <button
            type="button"
            className={`nav-tab ${page === "forecast" ? "nav-active" : ""}`}
            onClick={() => setPage("forecast")}
          >
            Forecast
          </button>
        </nav>
        <HealthBadge />
      </header>
      <main className="app-main">
        {page === "map" ? <MapPage /> : <ForecastPage />}
      </main>
    </div>
  );
}
