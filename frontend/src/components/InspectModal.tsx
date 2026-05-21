// InspectModal — the QC surface for a staged well set. Combines the
// gun-barrel cross-section (top, full width) with paired rate/cum
// overlay charts (bottom). User unchecks any wells that don't belong,
// then commits the remainder into the active cohort via "Add N to
// cohort". Cancel/Esc walks away without mutating the cohort.

import { useEffect, useMemo, useState } from "react";

import { fetchWellDetails, type WellDetailLite } from "../api/wells";
import { activeCohort, useCohortStore } from "../store/cohortStore";
import { GunBarrel } from "./GunBarrel";
import { InspectProductionCharts } from "./InspectProductionCharts";

export interface InspectModalProps {
  api14s: string[];
  onClose: () => void;
}

export function InspectModal({ api14s, onClose }: InspectModalProps) {
  const cohort = useCohortStore(activeCohort);
  const addApi14s = useCohortStore((s) => s.addApi14s);

  const [wells, setWells] = useState<WellDetailLite[] | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // All wells start checked — engineer un-ticks the ones to drop.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(api14s),
  );

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
    if (api14s.length === 0) {
      setWells([]);
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }
    setLoading(true);
    setError(null);
    fetchWellDetails(api14s)
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
  }, [api14s]);

  const wellsByApi14 = useMemo(() => {
    const m = new Map<string, WellDetailLite>();
    for (const w of wells ?? []) m.set(w.api14, w);
    return m;
  }, [wells]);

  function toggle(api14: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(api14)) next.delete(api14);
      else next.add(api14);
      return next;
    });
  }

  function commit() {
    if (!cohort) return;
    addApi14s(cohort.id, Array.from(selected));
    onClose();
  }

  const selectedCount = selected.size;
  const totalCount = api14s.length;
  const hasCohort = cohort != null;

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        // Backdrop click closes; clicks inside the modal body bubble
        // up to here too, so guard on currentTarget.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal inspect-modal" role="dialog" aria-modal="true">
        <div className="inspect-modal-header">
          <div className="inspect-modal-title">
            Inspect — {totalCount} well{totalCount === 1 ? "" : "s"} from staging
            {cohort && (
              <span className="inspect-modal-subtitle">
                → {cohort.name} ({cohort.api14s.length} in cohort)
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
                  selectedApi14s={selected}
                  onToggle={toggle}
                />
              </div>
              <div className="inspect-modal-section">
                <InspectProductionCharts
                  api14s={api14s}
                  wellsByApi14={wellsByApi14}
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
