// InspectModal — the QC surface for a staged well set. Combines the
// gun-barrel cross-section (top, full width) with paired rate/cum
// overlay charts (bottom). User unchecks any wells that don't belong,
// then commits the remainder into the active cohort via "Add N to
// cohort". Cancel/Esc walks away without mutating the cohort.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchWellDetails, type WellDetailLite } from "../api/wells";
import { activeCohort, useCohortStore } from "../store/cohortStore";
import { GunBarrel } from "./GunBarrel";
import { InspectProductionCharts } from "./InspectProductionCharts";

export interface InspectModalProps {
  api10s: string[];
  onClose: () => void;
}

export function InspectModal({ api10s, onClose }: InspectModalProps) {
  const cohort = useCohortStore(activeCohort);
  const addApi10s = useCohortStore((s) => s.addApi10s);

  const [wells, setWells] = useState<WellDetailLite[] | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

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

  const wellsByApi10 = useMemo(() => {
    const m = new Map<string, WellDetailLite>();
    for (const w of wells ?? []) m.set(w.api10, w);
    return m;
  }, [wells]);

  function toggle(api10: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(api10)) next.delete(api10);
      else next.add(api10);
      return next;
    });
  }

  function commit() {
    if (!cohort) return;
    addApi10s(cohort.id, Array.from(selected));
    onClose();
  }

  const selectedCount = selected.size;
  const totalCount = api10s.length;
  const hasCohort = cohort != null;

  return (
    // Floating (no backdrop) so the panel can be dragged aside while the
    // map/selection stays visible and interactive behind it — matching
    // ForecastDetailModal. Close via ✕ / Cancel / Esc.
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

        <div className="inspect-modal-body">
          {loading && (
            <div className="inspect-modal-loading">Loading wells…</div>
          )}
          {error && (
            <div className="inspect-modal-error">Failed: {error}</div>
          )}
          {!loading && !error && wells && (
            <>
              <div className="inspect-modal-section">
                <GunBarrel
                  wells={wells}
                  selectedApi10s={selected}
                  hoveredApi10={hoveredApi10}
                  onHover={setHoveredApi10}
                  onToggle={toggle}
                />
              </div>
              <div className="inspect-modal-section">
                <InspectProductionCharts
                  api10s={api10s}
                  wellsByApi10={wellsByApi10}
                  selectedApi10s={selected}
                  hoveredApi10={hoveredApi10}
                  onHover={setHoveredApi10}
                />
              </div>
            </>
          )}
        </div>

        <div className="inspect-modal-footer">
          <div className="inspect-modal-count">
            {selectedCount} of {totalCount} selected
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
  );
}
