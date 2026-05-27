import { useCallback, useEffect, useState } from "react";

import {
  type AuthUser,
  clearToken,
  fetchMe,
  getStoredToken,
  logout,
  setOnUnauthorized,
} from "./api/auth";
import { fetchTypeCurve } from "./api/typeCurves";
import type { FilterSpec, WellStatus } from "./api/types";
import { HealthBadge } from "./components/HealthBadge";
import { ForecastPage } from "./pages/ForecastPage";
import { LoginPage } from "./pages/LoginPage";
import { MapPage } from "./pages/MapPage";
import { ReviewPage } from "./pages/ReviewPage";
import { TypeCurvePage } from "./pages/TypeCurvePage";
import { TypeCurveSlidePage } from "./pages/TypeCurveSlidePage";
import { TypeCurveWellsPage } from "./pages/TypeCurveWellsPage";
import { useMapStore } from "./store/mapStore";

// Hash-based slide route. We don't use a router library; the slide
// is the only route that exits the normal app shell, so detecting it
// inline keeps the surface area small.
//   #/type-curves/<id>/slide
//   #/type-curves/<id>/slide?compareWith=<id>&probit=1
function parseSlideHash(): {
  typeCurveId: string;
  compareWithId: string | null;
  includeProbit: boolean;
} | null {
  const hash = window.location.hash || "";
  const m = hash.match(/^#\/type-curves\/([^/?]+)\/slide(?:\?(.+))?$/);
  if (!m) return null;
  let compareWithId: string | null = null;
  let includeProbit = false;
  if (m[2]) {
    const params = new URLSearchParams(m[2]);
    compareWithId = params.get("compareWith");
    includeProbit = params.get("probit") === "1";
  }
  return { typeCurveId: m[1]!, compareWithId, includeProbit };
}

// Hash route for the TC workspace (Phase 1: read-only well navigation).
//   #/type-curves/<id>/wells
function parseWellsHash(): { typeCurveId: string } | null {
  const hash = window.location.hash || "";
  const m = hash.match(/^#\/type-curves\/([^/?]+)\/wells$/);
  return m ? { typeCurveId: m[1]! } : null;
}

// Hash route for the TC add-wells flow (Phase 2.5). Lands the user on
// the Map tab with the TC's filter_spec pre-applied and the cohort
// bar replaced by an "Add N to TC: X" button.
//   #/type-curves/<id>/add-wells
function parseAddWellsHash(): { typeCurveId: string } | null {
  const hash = window.location.hash || "";
  const m = hash.match(/^#\/type-curves\/([^/?]+)\/add-wells$/);
  return m ? { typeCurveId: m[1]! } : null;
}

// Navigate by changing the URL hash and ALWAYS dispatching a
// hashchange event manually. Setting ``window.location.hash`` directly
// is unreliable across browsers when the new value is empty or only
// differs by a trailing "#" (Safari sometimes skips the event entirely),
// which lets the route-state hooks fall out of sync with the URL.
// Using history.pushState + a manual dispatch is consistent everywhere.
function navigateHash(newHash: string): void {
  const base = window.location.pathname + window.location.search;
  const target = newHash ? base + newHash : base;
  window.history.pushState(null, "", target);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
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
  const tcAddWellsMode = useMapStore((s) => s.tcAddWellsMode);
  const setTcAddWellsMode = useMapStore((s) => s.setTcAddWellsMode);
  const setFormations = useMapStore((s) => s.setFormations);
  const setOperators = useMapStore((s) => s.setOperators);
  const setStatuses = useMapStore((s) => s.setStatuses);
  const setVintageRange = useMapStore((s) => s.setVintageRange);
  const setLateralRange = useMapStore((s) => s.setLateralRange);
  const setApi14sFilter = useMapStore((s) => s.setApi14s);

  const [user, setUser] = useState<AuthUser | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);
  const [slideRoute, setSlideRoute] = useState(() => parseSlideHash());
  const [wellsRoute, setWellsRoute] = useState(() => parseWellsHash());
  const [addWellsRoute, setAddWellsRoute] = useState(() => parseAddWellsHash());

  // The slide tab is opened with window.open() into a new tab whose
  // URL already carries the hash, so hashchange isn't strictly needed
  // for the export flow — but listening for it keeps in-tab navigation
  // working if anyone deep-links via a manual edit. The wells +
  // add-wells routes share the listener.
  useEffect(() => {
    const onHash = () => {
      setSlideRoute(parseSlideHash());
      setWellsRoute(parseWellsHash());
      setAddWellsRoute(parseAddWellsHash());
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // When the add-wells route activates, fetch the TC and push its
  // filter_spec into the map store so the user starts looking at the
  // same wells the curve was built from. Also stash a tcAddWellsMode
  // marker so the cohort bar swaps its commit action.
  //
  // We don't reset the filter on exit — the user may want to keep
  // browsing with that filter applied. They can click "reset" in the
  // filter UI if not. We DO clear the mode marker on exit so the
  // cohort bar reverts to normal.
  useEffect(() => {
    if (!addWellsRoute) {
      setTcAddWellsMode(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const tc = await fetchTypeCurve(addWellsRoute.typeCurveId);
        if (cancelled) return;
        const f = (tc.filter_spec ?? {}) as Partial<FilterSpec>;
        setFormations(f.formations ?? []);
        setOperators(f.operators ?? []);
        setStatuses((f.statuses ?? []) as WellStatus[]);
        setVintageRange(f.first_prod_start ?? null, f.first_prod_end ?? null);
        setLateralRange(f.lateral_min_ft ?? null, f.lateral_max_ft ?? null);
        // api14s allow-list on the filter would force-include EXACTLY
        // those wells — that's the opposite of what we want when
        // adding NEW wells. Clear it so the user can browse the
        // broader cohort population.
        setApi14sFilter([]);
        setTcAddWellsMode({
          tcId: tc.id,
          tcName: tc.name,
          existingApi14s: new Set(tc.included_api14s ?? []),
        });
      } catch (e) {
        console.error("failed to enter add-wells mode", e);
        if (!cancelled) {
          // Bail out: clear hash and stay on whatever tab.
          navigateHash("");
          setTcAddWellsMode(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [addWellsRoute, setTcAddWellsMode, setFormations, setOperators, setStatuses, setVintageRange, setLateralRange, setApi14sFilter]);

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
        includeProbit={slideRoute.includeProbit}
      />
    );
  }

  // Add-wells route (Phase 2.5): lands the user on the Map page with
  // the TC's filter_spec pre-applied. The cohort bar reads
  // tcAddWellsMode from the store and swaps its commit action to
  // "Add N wells to TC: X". The bootstrap effect above runs the
  // fetch + filter push; while it's loading, tcAddWellsMode is null
  // and the page renders normally (filters might still be empty).
  if (addWellsRoute) {
    return (
      <div className="app-shell">
        <header className="app-header">
          <span className="app-title">Permian Type Curve</span>
          <nav className="app-nav">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`nav-tab ${t.id === "map" ? "nav-active" : ""}`}
                onClick={() => {
                  navigateHash("");
                  setPage(t.id);
                }}
              >
                {t.label}
              </button>
            ))}
          </nav>
          <div className="app-user">
            <HealthBadge />
            {tcAddWellsMode && (
              <span className="muted" style={{ fontSize: 12 }}>
                adding wells to <strong>{tcAddWellsMode.tcName}</strong>
              </span>
            )}
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                navigateHash(`#/type-curves/${addWellsRoute.typeCurveId}/wells`);
              }}
            >
              ← back to workspace
            </button>
            <span className="muted">{user.email}</span>
            <button type="button" className="link-btn" onClick={onLogout}>
              sign out
            </button>
          </div>
        </header>
        <main className="app-main">
          <MapPage />
        </main>
      </div>
    );
  }

  // Wells workspace route (TC Phase 1). Renders inside the app shell
  // so the user keeps the nav; "back" clears the hash and returns to
  // whatever tab they were on.
  if (wellsRoute) {
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
                onClick={() => {
                  navigateHash("");
                  setPage(t.id);
                }}
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
          <TypeCurveWellsPage
            typeCurveId={wellsRoute.typeCurveId}
            onExit={() => {
              navigateHash("");
            }}
          />
        </main>
      </div>
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
