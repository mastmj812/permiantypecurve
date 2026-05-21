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
import { TypeCurveSlidePage } from "./pages/TypeCurveSlidePage";
import { useMapStore } from "./store/mapStore";

// Hash-based slide route. We don't use a router library; the slide
// is the only route that exits the normal app shell, so detecting it
// inline keeps the surface area small.
//   #/type-curves/<id>/slide
//   #/type-curves/<id>/slide?compareWith=<id>
function parseSlideHash(): { typeCurveId: string; compareWithId: string | null } | null {
  const hash = window.location.hash || "";
  const m = hash.match(/^#\/type-curves\/([^/?]+)\/slide(?:\?(.+))?$/);
  if (!m) return null;
  let compareWithId: string | null = null;
  if (m[2]) {
    const params = new URLSearchParams(m[2]);
    compareWithId = params.get("compareWith");
  }
  return { typeCurveId: m[1]!, compareWithId };
}

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
  const [slideRoute, setSlideRoute] = useState(() => parseSlideHash());

  // The slide tab is opened with window.open() into a new tab whose
  // URL already carries the hash, so hashchange isn't strictly needed
  // for the export flow — but listening for it keeps in-tab navigation
  // working if anyone deep-links via a manual edit.
  useEffect(() => {
    const onHash = () => setSlideRoute(parseSlideHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

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

  // Slide route bypasses the app shell entirely so print output is
  // chrome-free. Authentication still required (handled above).
  if (slideRoute) {
    return (
      <TypeCurveSlidePage
        typeCurveId={slideRoute.typeCurveId}
        compareWithId={slideRoute.compareWithId}
      />
    );
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
