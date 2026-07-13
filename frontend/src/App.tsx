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
import { navigateHash } from "./navigation";
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
  dealVisibility: Record<string, boolean>;
} | null {
  const hash = window.location.hash || "";
  const m = hash.match(/^#\/type-curves\/([^/?]+)\/slide(?:\?(.+))?$/);
  if (!m) return null;
  let compareWithId: string | null = null;
  let includeProbit = false;
  // The slide runs in an iframe with its own (empty) mapStore, so the
  // Map tab's per-shapefile toggles reach it through the URL. `hideDeals`
  // is the pipe-joined set of source_files toggled OFF; rebuild it into
  // the `{source_file: false}` shape dealVisibilityFilter expects.
  const dealVisibility: Record<string, boolean> = {};
  if (m[2]) {
    const params = new URLSearchParams(m[2]);
    compareWithId = params.get("compareWith");
    includeProbit = params.get("probit") === "1";
    const hideDeals = params.get("hideDeals");
    if (hideDeals) {
      for (const sourceFile of hideDeals.split("|")) {
        if (sourceFile) dealVisibility[sourceFile] = false;
      }
    }
  }
  return { typeCurveId: m[1]!, compareWithId, includeProbit, dealVisibility };
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

// Hash route for the TC tab with a specific curve preloaded. Used by
// the workspace's "back to Type Curve" exit so the user lands back on
// the same curve they were editing instead of an empty TC tab.
//   #/type-curves/<id>
function parseTypeCurveDetailHash(): { typeCurveId: string } | null {
  const hash = window.location.hash || "";
  const m = hash.match(/^#\/type-curves\/([^/?]+)$/);
  return m ? { typeCurveId: m[1]! } : null;
}

const TABS: Array<{ id: "map" | "review" | "type_curve"; label: string }> = [
  { id: "map", label: "Map" },
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
  const setApi10sFilter = useMapStore((s) => s.setApi10s);

  const [user, setUser] = useState<AuthUser | null>(null);
  const [bootstrapped, setBootstrapped] = useState(false);
  const [slideRoute, setSlideRoute] = useState(() => parseSlideHash());
  const [wellsRoute, setWellsRoute] = useState(() => parseWellsHash());
  const [addWellsRoute, setAddWellsRoute] = useState(() => parseAddWellsHash());
  const [tcDetailRoute, setTcDetailRoute] = useState(() =>
    parseTypeCurveDetailHash(),
  );

  // The slide tab is opened with window.open() into a new tab whose
  // URL already carries the hash, so hashchange isn't strictly needed
  // for the export flow — but listening for it keeps in-tab navigation
  // working if anyone deep-links via a manual edit. The wells +
  // add-wells + tc-detail routes share the listener.
  useEffect(() => {
    const onHash = () => {
      setSlideRoute(parseSlideHash());
      setWellsRoute(parseWellsHash());
      setAddWellsRoute(parseAddWellsHash());
      setTcDetailRoute(parseTypeCurveDetailHash());
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
        // api10s allow-list on the filter would force-include EXACTLY
        // those wells — that's the opposite of what we want when
        // adding NEW wells. Clear it so the user can browse the
        // broader cohort population.
        setApi10sFilter([]);
        setTcAddWellsMode({
          tcId: tc.id,
          tcName: tc.name,
          existingApi10s: new Set(tc.included_api10s ?? []),
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
  }, [addWellsRoute, setTcAddWellsMode, setFormations, setOperators, setStatuses, setVintageRange, setLateralRange, setApi10sFilter]);

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
        dealVisibility={slideRoute.dealVisibility}
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
              // Land back on the Type Curve tab with this same curve
              // still loaded, not on a blank tab. The detail route is
              // a one-shot — TypeCurvePage's effect picks up the id,
              // fetches the curve, and the URL stays on
              // #/type-curves/{id} so a refresh restores the same
              // state.
              navigateHash(`#/type-curves/${wellsRoute.typeCurveId}`);
            }}
          />
        </main>
      </div>
    );
  }

  // When the URL carries a TC detail route (#/type-curves/{id}), force
  // the Type Curve tab and pass the id to TypeCurvePage so it
  // preloads. Used by the workspace's "back to Type Curve" exit so the
  // user lands on the same curve they were editing. Any nav-tab click
  // clears the hash, which drops the preload prop and lets the user
  // browse other tabs freely.
  const activePage = tcDetailRoute ? "type_curve" : page;
  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title">Permian Type Curve</span>
        <nav className="app-nav">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`nav-tab ${activePage === t.id ? "nav-active" : ""}`}
              onClick={() => {
                if (tcDetailRoute) navigateHash("");
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
        {activePage === "map" && <MapPage />}
        {activePage === "review" && <ReviewPage />}
        {activePage === "type_curve" && (
          <TypeCurvePage initialCurveId={tcDetailRoute?.typeCurveId ?? null} />
        )}
      </main>
    </div>
  );
}
