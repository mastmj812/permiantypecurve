// Manage modal for the narvi planned-stick overlay. Pick a narvi deal
// (all its saved scenarios render together), flip the master overlay
// toggle, and show/hide individual benches. Bench visibility filters
// ONLY the dashed stick layer — the PDP formation filters in the
// FilterPanel are deliberately independent.
//
// Overlay state (deal, payload, bench visibility, master toggle) lives
// in mapStore; local useState here covers only the scenario-list fetch
// lifecycle.

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { type NarviScenario, listNarviScenarios } from "../api/deals";
import { OTHER_COLOR, colorForFormation } from "../map/formations";
import { UNSET_BENCH, benchKey, benchKeysFor } from "../map/narviSticks";
import { useMapStore } from "../store/mapStore";

interface Props {
  onClose: () => void;
}

export function NarviSticksModal({ onClose }: Props) {
  const [scenarios, setScenarios] = useState<NarviScenario[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const narviDealId = useMapStore((s) => s.narviDealId);
  const setNarviDealId = useMapStore((s) => s.setNarviDealId);
  const showNarviSticks = useMapStore((s) => s.showNarviSticks);
  const setShowNarviSticks = useMapStore((s) => s.setShowNarviSticks);
  const narviSticks = useMapStore((s) => s.narviSticks);
  const narviBenchVisibility = useMapStore((s) => s.narviBenchVisibility);
  const setNarviBenchVisibility = useMapStore((s) => s.setNarviBenchVisibility);

  useEffect(() => {
    let cancelled = false;
    listNarviScenarios()
      .then((rows) => {
        if (!cancelled) setScenarios(rows);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // One pick-list entry per narvi deal_id (free text — narvi deal ids do
  // not correspond to anduin deal names). Preserves the newest-first
  // order of /api/narvi/scenarios.
  const deals = useMemo(() => {
    if (!scenarios) return [];
    const m = new Map<string, { scenarios: number; wells: number }>();
    for (const s of scenarios) {
      const d = m.get(s.deal_id) ?? { scenarios: 0, wells: 0 };
      d.scenarios += 1;
      d.wells += s.total_wells ?? 0;
      m.set(s.deal_id, d);
    }
    return Array.from(m.entries()).map(([dealId, d]) => ({ dealId, ...d }));
  }, [scenarios]);

  const dealScenarios = useMemo(
    () => (scenarios ?? []).filter((s) => s.deal_id === narviDealId),
    [scenarios, narviDealId],
  );

  const benchKeys = narviSticks ? benchKeysFor(narviSticks) : [];

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 640 }}
      >
        <header className="modal-header">
          <strong>Narvi planned sticks</strong>
          <button type="button" className="link-btn" onClick={onClose}>
            close
          </button>
        </header>
        <div className="modal-body">
          {error && (
            <div className="alert alert-error" style={{ marginBottom: 8 }}>
              {error}
            </div>
          )}
          {!scenarios && !error && <p className="muted">loading narvi scenarios…</p>}

          {scenarios && (
            <>
              <div className="toolbar-group" style={{ marginBottom: 12 }}>
                <span className="toolbar-label">Deal:</span>
                <select
                  value={narviDealId ?? ""}
                  onChange={(e) => setNarviDealId(e.target.value || null)}
                >
                  <option value="">— pick a narvi deal —</option>
                  {deals.map((d) => (
                    <option key={d.dealId} value={d.dealId}>
                      {d.dealId} ({d.scenarios} scenario{d.scenarios === 1 ? "" : "s"},{" "}
                      {d.wells} wells)
                    </option>
                  ))}
                </select>
                <label className="chk-inline" title="Show / hide the dashed planned sticks">
                  <input
                    type="checkbox"
                    checked={showNarviSticks}
                    disabled={narviDealId === null}
                    onChange={(e) => setShowNarviSticks(e.target.checked)}
                  />
                  Show on map
                </label>
              </div>

              {narviDealId && (
                <>
                  <p className="muted" style={{ fontSize: 11, marginTop: 0 }}>
                    Planned sticks only (PUD / UPSIDE) — existing producers are
                    the map&apos;s own solid wellsticks. Bench toggles below
                    affect the dashed sticks only, not the PDP formation
                    filters.
                  </p>

                  <div style={{ marginBottom: 14 }}>
                    <strong style={{ fontSize: 12 }}>Benches</strong>
                    {!narviSticks && (
                      <p className="muted" style={{ fontSize: 11 }}>
                        {showNarviSticks
                          ? "loading sticks…"
                          : "enable “Show on map” to load the deal’s benches"}
                      </p>
                    )}
                    {narviSticks && benchKeys.length === 0 && (
                      <p className="muted" style={{ fontSize: 11 }}>
                        no planned sticks in this deal
                      </p>
                    )}
                    {benchKeys.map((key) => {
                      const visible = narviBenchVisibility[key] !== false;
                      const color =
                        key === UNSET_BENCH ? OTHER_COLOR : colorForFormation(key);
                      const n = narviSticks
                        ? narviSticks.wells.filter((w) => benchKey(w.formation) === key)
                            .length
                        : 0;
                      return (
                        <label
                          key={key}
                          className="chk-inline"
                          style={{ display: "flex", alignItems: "center", gap: 6 }}
                          title="Show / hide this bench's planned sticks"
                        >
                          <input
                            type="checkbox"
                            checked={visible}
                            onChange={(e) => setNarviBenchVisibility(key, e.target.checked)}
                          />
                          <span
                            aria-hidden
                            style={{
                              width: 22,
                              borderTop: `3px dashed ${color}`,
                              display: "inline-block",
                            }}
                          />
                          {key}
                          <span className="muted" style={{ fontSize: 11 }}>
                            {n} well{n === 1 ? "" : "s"}
                          </span>
                        </label>
                      );
                    })}
                  </div>

                  <strong style={{ fontSize: 12 }}>Scenarios in this deal</strong>
                  <table className="deal-polygon-table">
                    <thead>
                      <tr>
                        <th>Scenario</th>
                        <th>Type</th>
                        <th>Wells</th>
                        <th>Updated</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dealScenarios.map((s) => (
                        <tr key={s.scenario_id}>
                          <td>{s.name ?? s.scenario_id}</td>
                          <td>{s.well_type}</td>
                          <td>{s.total_wells ?? "—"}</td>
                          <td>{s.updated_at.slice(0, 10)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
