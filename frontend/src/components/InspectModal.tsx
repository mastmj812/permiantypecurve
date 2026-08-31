// InspectModal — the QC surface for a staged well set. Combines the
// gun-barrel cross-section (top, full width) with paired rate/cum
// overlay charts (bottom). User unchecks any wells that don't belong,
// then commits the remainder into the active cohort via "Add N to
// cohort". Cancel/Esc walks away without mutating the cohort.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ExclusionEntry } from "../api/types";
import {
  fetchContextWells,
  fetchWellDetails,
  type WellDetailLite,
} from "../api/wells";
import { activeCohort, useCohortStore } from "../store/cohortStore";
import { useMapStore } from "../store/mapStore";
import { GunBarrel } from "./GunBarrel";
import { InspectProductionCharts } from "./InspectProductionCharts";
import { ReasonDialog } from "./ReasonDialog";

export interface InspectModalProps {
  api10s: string[];
  onClose: () => void;
}

export function InspectModal({ api10s, onClose }: InspectModalProps) {
  const cohort = useCohortStore(activeCohort);
  const addStaged = useCohortStore((s) => s.addStaged);
  const removeApi10s = useCohortStore((s) => s.removeApi10s);

  const [wells, setWells] = useState<WellDetailLite[] | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Greyed gun-barrel context: unfiltered neighbors of the staged set
  // (any formation/status), so co-development reads at a glance without
  // lassoing with filters off. Default on; failures degrade silently —
  // context is additive, never load-bearing.
  const [showContext, setShowContext] = useState<boolean>(true);
  const [contextWells, setContextWells] = useState<WellDetailLite[]>([]);

  // Reason dialog for "Remove N from cohort" — the coded reason rides
  // the manual_remove event into the build-up narrative.
  const [showRemoveDialog, setShowRemoveDialog] = useState(false);

  // Chart sizing driven by the modal's actual box — the modal is
  // CSS-resizable (bottom-right grip), and the SVGs re-scale with it
  // instead of just gaining scroll room. Body content box observed so
  // padding/scrollbar are already excluded.
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [bodySize, setBodySize] = useState<{ w: number; h: number } | null>(
    null,
  );
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const box = entry.contentBoxSize?.[0];
        if (box) setBodySize({ w: box.inlineSize, h: box.blockSize });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Gun-barrel fills the body width; heights split the body's real
  // height (flex:1 in CSS makes it track the resized modal box) minus
  // ~110px of fixed chrome (context label, stream/scale pills, gaps).
  // Width margins are deliberately generous so borders + the reserved
  // scrollbar gutter can never trip horizontal overflow. Floors keep
  // the pre-resize sizes as minimums.
  const chromeH = 110;
  const availH = bodySize ? Math.max(0, bodySize.h - chromeH) : 0;
  const gbWidth = bodySize ? Math.max(600, Math.floor(bodySize.w) - 24) : 880;
  const gbHeight = bodySize ? Math.max(320, Math.round(availH * 0.5)) : 320;
  const chartWidth = bodySize
    ? Math.max(380, Math.floor((bodySize.w - 40) / 2))
    : 430;
  const chartHeight = bodySize
    ? Math.max(240, Math.round(availH * 0.42))
    : 240;

  // All wells start checked — engineer un-ticks the ones to drop.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(api10s),
  );

  // Lifted hover state so the gun-barrel and the rate/cum charts stay
  // in lockstep: hovering a circle bolds the corresponding line (and
  // vice versa). Null when nothing is hovered.
  const [hoveredApi10, setHoveredApi10] = useState<string | null>(null);

  // Drag state — same free-floating convention as ForecastDetailModal
  // (and the erebor/narvi gunbarrel windows): grab the header to move
  // the panel aside and see the wells on the map behind it. Position
  // is a translate offset from the initial centered render.
  const [dragPos, setDragPos] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const dragOriginRef = useRef<{
    mouseX: number;
    mouseY: number;
    startX: number;
    startY: number;
  } | null>(null);

  const onDragHandleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLElement>) => {
      // Only the header background starts a drag — ignore the close
      // button (and any future header controls) so clicks still work.
      const target = e.target as HTMLElement;
      if (target.closest("button") || target.closest("input")) return;
      dragOriginRef.current = {
        mouseX: e.clientX,
        mouseY: e.clientY,
        startX: dragPos.x,
        startY: dragPos.y,
      };
      e.preventDefault();
    },
    [dragPos.x, dragPos.y],
  );

  useEffect(() => {
    function onMove(e: MouseEvent) {
      const o = dragOriginRef.current;
      if (!o) return;
      setDragPos({
        x: o.startX + (e.clientX - o.mouseX),
        y: o.startY + (e.clientY - o.mouseY),
      });
    }
    function onUp() {
      dragOriginRef.current = null;
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  // ESC closes, same convention as ForecastDetailModal. Skip when
  // focus is in an input so we don't fight inputs in nested controls.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) {
        return;
      }
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Fetch detail bundles for all staged wells (one call, batched).
  // Same set drives both the gun-barrel circles and the production
  // chart formation colors + per-10kft scaling.
  useEffect(() => {
    let cancelled = false;
    if (api10s.length === 0) {
      setWells([]);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
    setLoading(true);
    setError(null);
    fetchWellDetails(api10s)
      .then((rows) => {
        if (!cancelled) setWells(rows);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api10s]);

  // Context fetch — once per staged set, independent of the detail
  // fetch so a slow context query never delays the staged circles.
  // Population rule: when the inspected set came from a draw (the usual
  // lasso/strike-through → Inspect habit), context = the unfiltered
  // wells of THAT footprint — a strike-through shows exactly its
  // section, not everything within a radius of the laterals (which
  // pulled in the next lease over). The overlap guard keeps a stale
  // polygon drawn elsewhere from defining a bogus footprint; with no
  // relevant draw the server falls back to the 3,000-ft radius.
  useEffect(() => {
    let cancelled = false;
    if (api10s.length === 0) {
      setContextWells([]);
      return () => {
        cancelled = true;
      };
    }
    const lastDraw = useMapStore.getState().lastDraw;
    const fromDraw =
      lastDraw != null && api10s.some((a) => lastDraw.api10s.includes(a));
    fetchContextWells(api10s, {
      polygon: fromDraw ? lastDraw.polygon : null,
    })
      .then((rows) => {
        if (!cancelled) setContextWells(rows);
      })
      .catch(() => {
        if (!cancelled) setContextWells([]);
      });
    return () => {
      cancelled = true;
    };
  }, [api10s]);

  const wellsByApi10 = useMemo(() => {
    const m = new Map<string, WellDetailLite>();
    for (const w of wells ?? []) m.set(w.api10, w);
    return m;
  }, [wells]);

  // api10s already in the active cohort — drives the sky-blue membership
  // halo in the gun-barrel + production overlays, so a staged well that's
  // already a cohort member reads at a glance (same cue as the map).
  const cohortApi10s = useMemo(
    () => new Set(cohort?.api10s ?? []),
    [cohort?.api10s],
  );

  function toggle(api10: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(api10)) next.delete(api10);
      else next.add(api10);
      return next;
    });
  }

  // Box-select handoff from the gun-barrel. replace = only the boxed
  // wells; add/subtract = union/difference with the current selection.
  const boxSelect = useCallback(
    (list: string[], mode: "replace" | "add" | "subtract") => {
      setSelected((prev) => {
        if (mode === "replace") return new Set(list);
        const next = new Set(prev);
        if (mode === "add") for (const a of list) next.add(a);
        else for (const a of list) next.delete(a);
        return next;
      });
    },
    [],
  );

  const selectAll = useCallback(() => setSelected(new Set(api10s)), [api10s]);
  const selectNone = useCallback(() => setSelected(new Set()), []);

  function commit() {
    if (!cohort) return;
    // The inspected set almost always CAME from a lasso (the engineer's
    // habit: lasso → gun-barrel QC → add the keepers). Pass the live
    // draw so those wells attribute the AOI polygon to the build-up —
    // the first bro_time export lost its whole starting universe
    // because this path dropped it. Wells not in the draw still log as
    // click_adds inside buildStagedEvents.
    addStaged(
      cohort.id,
      Array.from(selected),
      useMapStore.getState().lastDraw,
    );
    onClose();
  }

  // Remove only the selected wells that are actually in the cohort — a
  // selected well that was never a member is a no-op, so the button
  // count reflects what will really change.
  const selectedInCohort = useMemo(() => {
    if (!cohort) return [];
    const members = new Set(cohort.api10s);
    return Array.from(selected).filter((a) => members.has(a));
  }, [cohort, selected]);

  function removeFromCohort(reason: ExclusionEntry) {
    if (!cohort || selectedInCohort.length === 0) return;
    removeApi10s(cohort.id, selectedInCohort, reason);
    setShowRemoveDialog(false);
    onClose();
  }

  const selectedCount = selected.size;
  const totalCount = api10s.length;
  const hasCohort = cohort != null;
  const removeCount = selectedInCohort.length;

  return (
    // Floating (no backdrop) so the panel can be dragged aside while the
    // map/selection stays visible and interactive behind it — matching
    // ForecastDetailModal. Close via ✕ / Cancel / Esc.
    <>
    <div className="modal-floating-wrap">
      <div
        className="modal inspect-modal modal-floating"
        role="dialog"
        aria-modal="true"
        style={{ transform: `translate(${dragPos.x}px, ${dragPos.y}px)` }}
      >
        <div
          className="inspect-modal-header modal-drag-handle"
          onMouseDown={onDragHandleMouseDown}
        >
          <div className="inspect-modal-title">
            Inspect — {totalCount} well{totalCount === 1 ? "" : "s"} from staging
            {cohort && (
              <span className="inspect-modal-subtitle">
                → {cohort.name} ({cohort.api10s.length} in cohort)
              </span>
            )}
          </div>
          <button
            type="button"
            className="inspect-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="inspect-modal-body" ref={bodyRef}>
          {loading && (
            <div className="inspect-modal-loading">Loading wells…</div>
          )}
          {error && (
            <div className="inspect-modal-error">Failed: {error}</div>
          )}
          {!loading && !error && wells && (
            <>
              {/* Column layout: the context-wells toggle sits UNDER the
                  chart — beside a full-width gun-barrel it collapses
                  into an unreadable one-character-wide strip. */}
              <div
                className="inspect-modal-section"
                style={{ flexDirection: "column", alignItems: "center", gap: 4 }}
              >
                <GunBarrel
                  wells={wells}
                  contextWells={showContext ? contextWells : undefined}
                  selectedApi10s={selected}
                  cohortApi10s={cohortApi10s}
                  hoveredApi10={hoveredApi10}
                  onHover={setHoveredApi10}
                  onToggle={toggle}
                  onBoxSelect={boxSelect}
                  width={gbWidth}
                  height={gbHeight}
                />
                <label
                  className="muted"
                  style={{ display: "inline-flex", gap: 4 }}
                  title="Unfiltered wells of the footprint you drew (any formation/status, map filters ignored) — greyed, display-only, never staged. Falls back to a 3,000-ft radius when the inspected set wasn't drawn."
                >
                  <input
                    type="checkbox"
                    checked={showContext}
                    onChange={(e) => setShowContext(e.target.checked)}
                  />
                  context wells (grey = in your draw footprint, any formation)
                </label>
              </div>
              <div className="inspect-modal-section">
                <InspectProductionCharts
                  api10s={api10s}
                  wellsByApi10={wellsByApi10}
                  selectedApi10s={selected}
                  cohortApi10s={cohortApi10s}
                  hoveredApi10={hoveredApi10}
                  onHover={setHoveredApi10}
                  chartWidth={chartWidth}
                  chartHeight={chartHeight}
                />
              </div>
            </>
          )}
        </div>

        <div className="inspect-modal-footer">
          <div className="inspect-modal-count">
            {selectedCount} of {totalCount} selected
            <button
              type="button"
              className="inspect-modal-linkbtn"
              onClick={selectAll}
              disabled={selectedCount === totalCount}
            >
              all
            </button>
            <button
              type="button"
              className="inspect-modal-linkbtn"
              onClick={selectNone}
              disabled={selectedCount === 0}
            >
              none
            </button>
            <span className="inspect-modal-hint">
              drag to box-select · shift add · alt remove
            </span>
          </div>
          <div className="inspect-modal-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              disabled={!hasCohort || removeCount === 0}
              onClick={() => setShowRemoveDialog(true)}
              title={
                !hasCohort
                  ? "Create a cohort first"
                  : removeCount === 0
                    ? "None of the selected wells are in the cohort"
                    : `Remove ${removeCount} well${
                        removeCount === 1 ? "" : "s"
                      } from ${cohort?.name ?? "the cohort"}`
              }
            >
              Remove {removeCount} from cohort
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!hasCohort || selectedCount === 0}
              onClick={commit}
              title={
                !hasCohort
                  ? "Create a cohort first"
                  : selectedCount === 0
                    ? "Select at least one well"
                    : ""
              }
            >
              Add {selectedCount} to cohort
            </button>
          </div>
        </div>
      </div>
    </div>
    {/* OUTSIDE the floating wrap: .modal-floating-wrap is pointer-events:
        none (only the inner .modal-floating re-enables them), so a dialog
        nested inside it renders but can't be clicked. */}
    {showRemoveDialog && cohort && (
      <ReasonDialog
        title={`Remove ${removeCount} well${removeCount === 1 ? "" : "s"} from ${cohort.name}`}
        detail="One code for the batch — the build-up sheet shows it on not-selected unless a filter stage already explains the cull (nuance goes in the note)."
        confirmLabel={`Remove ${removeCount}`}
        onConfirm={removeFromCohort}
        onCancel={() => setShowRemoveDialog(false)}
      />
    )}
    </>
  );
}
