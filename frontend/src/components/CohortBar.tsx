// CohortBar — horizontal strip above the map showing the currently
// active cohort, with action buttons for the iterative pad-by-pad
// curve-building workflow. Two states:
//
//   * No active cohort: narrow bar with [+ New cohort] + muted note
//     "no cohort active — current selection will forecast directly".
//     The legacy lasso → forecast flow keeps working unchanged.
//
//   * Active cohort: name + count + deal + action buttons:
//       [Add staged (N)]   adds mapStore.selectedApi10s to the cohort
//       [Remove staged (N)] removes any staged wells from the cohort
//       [Inspect (N)]      opens the gun-barrel modal on the staged set
//       [Forecast]         pushes cohort.api10s to mapStore.forecastApi10s
//                          + stashes name+deal_id on the handoff fields,
//                          navigates to the Forecast tab. Deliberate
//                          handoff — no confirmation.
//       [▾]                switcher dropdown (other cohorts, + New)
//       [⋯]                overflow menu (rename, change deal, delete)
//
// Cohorts persist via the zustand persist middleware in cohortStore;
// this component is a thin UI shell over those mutations.

import { useEffect, useMemo, useRef, useState } from "react";

import { listDeals, type DealSummary } from "../api/deals";
import { patchTypeCurveMembership } from "../api/typeCurves";
import {
  activeCohort,
  useCohortStore,
  type Cohort,
} from "../store/cohortStore";
import { useMapStore } from "../store/mapStore";

export function CohortBar() {
  const cohorts = useCohortStore((s) => s.cohorts);
  const active = useCohortStore(activeCohort);
  const setActive = useCohortStore((s) => s.setActive);
  const addApi10s = useCohortStore((s) => s.addApi10s);
  const removeApi10s = useCohortStore((s) => s.removeApi10s);
  const renameCohort = useCohortStore((s) => s.rename);
  const setCohortDeal = useCohortStore((s) => s.setDeal);
  const replaceApi10s = useCohortStore((s) => s.replaceApi10s);
  const deleteCohort = useCohortStore((s) => s.deleteCohort);

  const selectedApi10s = useMapStore((s) => s.selectedApi10s);
  const setForecastApi10s = useMapStore((s) => s.setForecastApi10s);
  const setCohortHandoff = useMapStore((s) => s.setCohortHandoff);
  const setCurrentPage = useMapStore((s) => s.setCurrentPage);
  const tcAddWellsMode = useMapStore((s) => s.tcAddWellsMode);
  const setTcAddWellsMode = useMapStore((s) => s.setTcAddWellsMode);
  const clearSelection = useMapStore((s) => s.clearSelection);
  const [addBusy, setAddBusy] = useState(false);
  const [addErr, setAddErr] = useState<string | null>(null);

  const [showNewModal, setShowNewModal] = useState(false);
  const [showSwitcher, setShowSwitcher] = useState(false);
  const [showOverflow, setShowOverflow] = useState(false);

  // Memoize the staged api10 array — cohort mutations care about the
  // values, not the Set identity. Avoids rebuilding identical arrays on
  // every render.
  const stagedArray = useMemo(
    () => Array.from(selectedApi10s),
    [selectedApi10s],
  );
  const stagedCount = stagedArray.length;

  // How many of the currently-staged wells are already in the cohort?
  // Drives the [Remove staged] button's enabled state — no point
  // offering to remove staged wells that aren't in the cohort to begin
  // with.
  const stagedInCohort = useMemo(() => {
    if (!active) return 0;
    const set = new Set(active.api10s);
    let n = 0;
    for (const a of stagedArray) if (set.has(a)) n++;
    return n;
  }, [active, stagedArray]);

  // -------- Inspect modal trigger (the modal itself lives in MapPage) --
  // We dispatch a custom event the modal listens for so the CohortBar
  // doesn't have to thread modal-open state all the way through. Keeps
  // the bar focused on its own concerns.
  const openInspect = () => {
    if (stagedCount === 0) return;
    window.dispatchEvent(
      new CustomEvent("cohort:open-inspect", {
        detail: { api10s: stagedArray },
      }),
    );
  };

  const handleForecast = () => {
    if (!active || active.api10s.length === 0) return;
    setForecastApi10s(active.api10s);
    setCohortHandoff(active.name, active.deal_id);
    // One-shot trigger — Review consumes this to fire the batch.
    // Plain tab navigation doesn't set it.
    useMapStore.getState().setForecastTriggerPending(true);
    setCurrentPage("review");
  };

  // -------- TC add-wells mode: hijack the bar for membership PATCH ---
  // Replaces the cohort UI entirely when the user came in via the
  // `#/type-curves/{id}/add-wells` route. The cohort store is left
  // alone — this is a separate flow that targets the TC directly.
  if (tcAddWellsMode) {
    const newWells = stagedArray.filter(
      (a) => !tcAddWellsMode.existingApi10s.has(a),
    );
    const dupCount = stagedCount - newWells.length;
    const tcId = tcAddWellsMode.tcId;
    const tcName = tcAddWellsMode.tcName;

    async function handleAddToTc() {
      if (newWells.length === 0 || addBusy) return;
      setAddBusy(true);
      setAddErr(null);
      try {
        await patchTypeCurveMembership(tcId, { add: newWells });
        // Clear local state + leave the mode; navigate back to workspace.
        clearSelection();
        setTcAddWellsMode(null);
        // Manual hashchange dispatch — see App.tsx::navigateHash for why
        // setting window.location.hash alone is unreliable across browsers.
        const base = window.location.pathname + window.location.search;
        window.history.pushState(null, "", `${base}#/type-curves/${tcId}/wells`);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      } catch (e) {
        setAddErr(e instanceof Error ? e.message : String(e));
      } finally {
        setAddBusy(false);
      }
    }

    function handleCancel() {
      setTcAddWellsMode(null);
      window.location.hash = `#/type-curves/${tcId}/wells`;
    }

    return (
      <div className="cohort-bar" style={{ background: "#eff6ff" }}>
        <div className="cohort-bar-label">
          <strong>Add wells to {tcName}</strong>
          <span className="muted">
            · {tcAddWellsMode.existingApi10s.size} already in TC
          </span>
          <span className="muted">
            · lasso / box / click wells on the map to stage them
          </span>
        </div>
        <div className="cohort-bar-actions">
          {dupCount > 0 && (
            <span className="muted" style={{ fontSize: 11 }}>
              {dupCount} staged well{dupCount === 1 ? "" : "s"} already in TC
              (will be ignored)
            </span>
          )}
          {addErr && (
            <span className="alert alert-error" style={{ fontSize: 11 }}>
              {addErr}
            </span>
          )}
          <button
            type="button"
            className="btn-primary"
            disabled={newWells.length === 0 || addBusy}
            title={
              newWells.length === 0
                ? "Stage at least one well that isn't already in the TC"
                : `Add ${newWells.length} new well${newWells.length === 1 ? "" : "s"} to ${tcName}`
            }
            onClick={() => void handleAddToTc()}
          >
            {addBusy
              ? "adding…"
              : `Add ${newWells.length} to TC`}
          </button>
          <button type="button" className="tb-btn" onClick={handleCancel}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  // -------- No active cohort: minimal bar -----------------------------
  if (!active) {
    return (
      <>
        <div className="cohort-bar cohort-bar-empty">
          <button
            type="button"
            className="tb-btn"
            onClick={() => setShowNewModal(true)}
          >
            + New cohort
          </button>
          <span className="muted" style={{ fontSize: 11 }}>
            no cohort active — current selection will forecast directly
          </span>
        </div>
        {showNewModal && (
          <NewCohortModal
            initialStagedApi10s={stagedArray}
            onClose={() => setShowNewModal(false)}
          />
        )}
      </>
    );
  }

  // -------- Active cohort: full bar -----------------------------------
  return (
    <>
      <div className="cohort-bar">
        <div className="cohort-bar-label">
          <strong>{active.name}</strong>
          <span className="muted">
            · {active.api10s.length} well{active.api10s.length === 1 ? "" : "s"}
          </span>
          {active.deal_id && (
            <span className="muted">· deal assigned</span>
          )}
          <CohortSwitcher
            cohorts={cohorts}
            activeId={active.id}
            open={showSwitcher}
            onToggle={() => setShowSwitcher((v) => !v)}
            onClose={() => setShowSwitcher(false)}
            onSwitch={(id) => {
              setActive(id);
              setShowSwitcher(false);
            }}
            onNew={() => {
              setShowSwitcher(false);
              setShowNewModal(true);
            }}
          />
        </div>

        <div className="cohort-bar-actions">
          <button
            type="button"
            className="tb-btn"
            disabled={stagedCount === 0}
            title={
              stagedCount === 0
                ? "Lasso some wells on the map to stage them first"
                : `Add ${stagedCount} staged well${stagedCount === 1 ? "" : "s"} to the cohort`
            }
            onClick={() => addApi10s(active.id, stagedArray)}
          >
            Add staged ({stagedCount})
          </button>
          <button
            type="button"
            className="tb-btn"
            disabled={stagedInCohort === 0}
            title={
              stagedInCohort === 0
                ? "None of the staged wells are in this cohort"
                : `Remove ${stagedInCohort} staged well${stagedInCohort === 1 ? "" : "s"} from the cohort`
            }
            onClick={() => removeApi10s(active.id, stagedArray)}
          >
            Remove staged ({stagedInCohort})
          </button>
          <button
            type="button"
            className="tb-btn"
            disabled={stagedCount === 0}
            title={
              stagedCount === 0
                ? "Lasso wells to inspect them in the gun-barrel"
                : "Open gun-barrel + production charts on the staged wells"
            }
            onClick={openInspect}
          >
            Inspect ({stagedCount})
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={active.api10s.length === 0}
            title={
              active.api10s.length === 0
                ? "Add at least one well to forecast"
                : "Forecast every well in this cohort"
            }
            onClick={handleForecast}
          >
            Forecast
          </button>
          <button
            type="button"
            className="tb-btn"
            aria-label="Cohort menu"
            onClick={() => setShowOverflow((v) => !v)}
          >
            ⋯
          </button>
          {showOverflow && (
            <CohortOverflow
              cohort={active}
              onClose={() => setShowOverflow(false)}
              onRename={(name) => renameCohort(active.id, name)}
              onSetDeal={(id) => setCohortDeal(active.id, id)}
              onClear={() => {
                replaceApi10s(active.id, []);
                setShowOverflow(false);
              }}
              onDelete={() => {
                deleteCohort(active.id);
                setShowOverflow(false);
              }}
            />
          )}
        </div>
      </div>

      {showNewModal && (
        <NewCohortModal
          initialStagedApi10s={stagedArray}
          onClose={() => setShowNewModal(false)}
        />
      )}
    </>
  );
}

// ---------------------- switcher dropdown ----------------------------

function CohortSwitcher({
  cohorts,
  activeId,
  open,
  onToggle,
  onClose,
  onSwitch,
  onNew,
}: {
  cohorts: Cohort[];
  activeId: string;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onSwitch: (id: string) => void;
  onNew: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [query, setQuery] = useState("");
  // Close on outside-click. Stays open while clicking inside the menu.
  useEffect(() => {
    if (!open) return;
    setQuery("");
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open, onClose]);

  const q = query.trim().toLowerCase();
  const filtered = q
    ? cohorts.filter((c) => c.name.toLowerCase().includes(q))
    : cohorts;

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className="tb-btn"
        aria-label="Switch cohort"
        onClick={onToggle}
      >
        ▾
      </button>
      {open && (
        <div className="cohort-switcher-menu">
          {cohorts.length > 0 && (
            <div className="cohort-switcher-search">
              <input
                type="text"
                autoFocus
                value={query}
                placeholder="Search cohorts…"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") onClose();
                  const first = filtered[0];
                  if (e.key === "Enter" && first) onSwitch(first.id);
                }}
              />
            </div>
          )}
          <div className="cohort-switcher-list">
            {cohorts.length === 0 && (
              <div className="cohort-switcher-empty">No cohorts yet.</div>
            )}
            {cohorts.length > 0 && filtered.length === 0 && (
              <div className="cohort-switcher-empty">No matches.</div>
            )}
            {filtered.map((c) => (
              <button
                key={c.id}
                type="button"
                className={
                  c.id === activeId
                    ? "cohort-switcher-item active"
                    : "cohort-switcher-item"
                }
                onClick={() => onSwitch(c.id)}
              >
                <span className="cohort-switcher-name">{c.name}</span>
                <span className="muted">{c.api10s.length}</span>
              </button>
            ))}
          </div>
          <div className="cohort-switcher-divider" />
          <button
            type="button"
            className="cohort-switcher-item"
            onClick={onNew}
          >
            + New cohort
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------- overflow menu --------------------------------

function CohortOverflow({
  cohort,
  onClose,
  onRename,
  onSetDeal,
  onClear,
  onDelete,
}: {
  cohort: Cohort;
  onClose: () => void;
  onRename: (name: string) => void;
  onSetDeal: (deal_id: string | null) => void;
  onClear: () => void;
  onDelete: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [deals, setDeals] = useState<DealSummary[]>([]);

  useEffect(() => {
    listDeals().then(setDeals).catch(() => setDeals([]));
  }, []);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [onClose]);

  return (
    <div ref={ref} className="cohort-overflow-menu">
      <button
        type="button"
        className="cohort-switcher-item"
        onClick={() => {
          const next = window.prompt("New cohort name:", cohort.name);
          if (next && next.trim() && next.trim() !== cohort.name) {
            onRename(next.trim());
          }
          onClose();
        }}
      >
        Rename
      </button>
      <div className="cohort-overflow-deal">
        <label className="muted" style={{ fontSize: 11 }}>
          Deal
        </label>
        <select
          value={cohort.deal_id ?? ""}
          onChange={(e) => onSetDeal(e.target.value || null)}
        >
          <option value="">(unassigned)</option>
          {deals.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </div>
      <div className="cohort-switcher-divider" />
      <button
        type="button"
        className="cohort-switcher-item"
        disabled={cohort.api10s.length === 0}
        onClick={() => {
          if (
            window.confirm(
              `Clear all ${cohort.api10s.length} well${
                cohort.api10s.length === 1 ? "" : "s"
              } from "${cohort.name}"? The cohort name and deal stay; you can rebuild from scratch.`,
            )
          ) {
            onClear();
          }
        }}
      >
        Clear wells
      </button>
      <button
        type="button"
        className="cohort-switcher-item cohort-switcher-danger"
        onClick={() => {
          if (
            window.confirm(
              `Delete cohort "${cohort.name}"? The wells stay in the database; only this working list goes away.`,
            )
          ) {
            onDelete();
          }
        }}
      >
        Delete cohort
      </button>
    </div>
  );
}

// ---------------------- new-cohort modal -----------------------------

function NewCohortModal({
  initialStagedApi10s,
  onClose,
}: {
  initialStagedApi10s: string[];
  onClose: () => void;
}) {
  const cohorts = useCohortStore((s) => s.cohorts);
  const createCohort = useCohortStore((s) => s.createCohort);
  const [name, setName] = useState("");
  const [dealId, setDealId] = useState<string>("");
  const [seedFromStaged, setSeedFromStaged] = useState(
    initialStagedApi10s.length > 0,
  );
  const [deals, setDeals] = useState<DealSummary[]>([]);

  useEffect(() => {
    listDeals().then(setDeals).catch(() => setDeals([]));
  }, []);

  const nameTrimmed = name.trim();
  const nameTaken = useMemo(
    () => cohorts.some((c) => c.name.toLowerCase() === nameTrimmed.toLowerCase()),
    [cohorts, nameTrimmed],
  );
  const canCreate = nameTrimmed.length > 0 && !nameTaken;

  function handleCreate() {
    if (!canCreate) return;
    createCohort({
      name: nameTrimmed,
      deal_id: dealId || null,
      initial_api10s: seedFromStaged ? initialStagedApi10s : [],
    });
    onClose();
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal cohort-new-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <strong>New cohort</strong>
          <button type="button" className="link-btn" onClick={onClose}>
            close
          </button>
        </header>
        <div className="modal-body" style={{ display: "grid", gap: 12 }}>
          <label className="stat" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <span className="stat-label">Name</span>
            <input
              type="text"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canCreate) handleCreate();
              }}
              placeholder="e.g. holdTheLine_bs1s_v3"
              style={{ marginTop: 4 }}
            />
            {nameTaken && (
              <span className="muted err" style={{ fontSize: 11 }}>
                A cohort with this name already exists.
              </span>
            )}
          </label>

          <label className="stat" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <span className="stat-label">Deal (optional)</span>
            <select
              value={dealId}
              onChange={(e) => setDealId(e.target.value)}
              style={{ marginTop: 4 }}
            >
              <option value="">(none)</option>
              {deals.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>

          {initialStagedApi10s.length > 0 && (
            <label className="chk-inline">
              <input
                type="checkbox"
                checked={seedFromStaged}
                onChange={(e) => setSeedFromStaged(e.target.checked)}
              />
              Seed with the {initialStagedApi10s.length} currently-staged well
              {initialStagedApi10s.length === 1 ? "" : "s"}
            </label>
          )}

          <div className="param-actions" style={{ marginTop: 8 }}>
            <button
              type="button"
              className="btn-primary"
              disabled={!canCreate}
              onClick={handleCreate}
            >
              Create
            </button>
            <button type="button" className="tb-btn" onClick={onClose}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
