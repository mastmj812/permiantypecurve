import { useCallback, useEffect, useState } from "react";

import {
  type AuthUser,
  clearToken,
  fetchMe,
  getStoredToken,
  logout,
  setOnUnauthorized,
} from "./api/auth";
import { HealthBadge } from "./components/HealthBadge";
import { ForecastPage } from "./pages/ForecastPage";
import { LoginPage } from "./pages/LoginPage";
import { MapPage } from "./pages/MapPage";
import { ReviewPage } from "./pages/ReviewPage";
import { TypeCurvePage } from "./pages/TypeCurvePage";
import { useMapStore } from "./store/mapStore";

const TABS: Array<{ id: "map" | "forecast" | "review" | "type_curve"; label: string }> = [
  { id: "map", label: "Map" },
  { id: "forecast", label: "Forecast" },
  { id: "review", label: "Review" },
  { id: "type_curve", label: "Type curve" },
];

export function App() {
  const page = useMapStore((s) => s.currentPage);
  const setPage = useMapStore((s) => s.setCurrentPage);

  const [user, setUser] = useState<AuthUser | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);

  // On mount, if a token exists, verify it via /me. Otherwise stay logged out.
  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      setBootstrapped(true);
      return;
    }
    fetchMe()
      .then(setUser)
      .catch(() => {
        clearToken();
        setUser(null);
      })
      .finally(() => setBootstrapped(true));
  }, []);

  // Wire the 401 handler so any apiFetch 401 boots us back to login.
  useEffect(() => {
    setOnUnauthorized(() => setUser(null));
  }, []);

  const onLogout = useCallback(async () => {
    await logout();
    setUser(null);
  }, []);

  if (!bootstrapped) {
    return <div className="boot-splash">…</div>;
  }
  if (!user) {
    return <LoginPage onAuthenticated={setUser} />;
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title">Permian Type Curve</span>
        <nav className="app-nav">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`nav-tab ${page === t.id ? "nav-active" : ""}`}
              onClick={() => setPage(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div className="app-user">
          <HealthBadge />
          <span className="muted">{user.email}</span>
          <button type="button" className="link-btn" onClick={onLogout}>
            sign out
          </button>
        </div>
      </header>
      <main className="app-main">
        {page === "map" && <MapPage />}
        {page === "forecast" && <ForecastPage />}
        {page === "review" && <ReviewPage />}
        {page === "type_curve" && <TypeCurvePage />}
      </main>
    </div>
  );
}
