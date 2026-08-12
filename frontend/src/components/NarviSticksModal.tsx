// Manage modal for the narvi planned-stick overlay. narvi deal_ids are
// per-DSU in practice (an engineer's "deal" like vault spans many
// deal_id rows: vault_dsu_*), so selection is a SET of deals — filter
// the list (e.g. type "vault"), select all shown, and the bench
// toggles below span the whole selection. Bench visibility filters
// ONLY the dashed stick layer — the PDP formation filters in the
// FilterPanel are deliberately independent.
//
// Overlay state (deal set, payload, bench visibility, master toggle)
// lives in mapStore; local useState here covers only the scenario-list
// fetch lifecycle and the filter text.

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
  const [filter, setFilter] = useState("");

  const narviDealIds = useMapStore((s) => s.narviDealIds);
  const setNarviDealIds = useMapStore((s) => s.setNarviDealIds);
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

  // One row per narvi deal_id (free text — one per DSU in practice).
  // Preserves the newest-first order of /api/narvi/scenarios.
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

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return deals;
    return deals.filter((d) => d.dealId.toLowerCase().includes(q));
  }, [deals, filter]);

  const selected = useMemo(() => new Set(narviDealIds), [narviDealIds]);

  function toggleDeal(dealId: string, on: boolean) {
    if (on) setNarviDealIds([...narviDealIds, dealId]);
    else setNarviDealIds(narviDealIds.filter((d) => d !== dealId));
  }

  function selectAllShown() {
    setNarviDealIds([
      ...new Set([...narviDealIds, ...shown.map((d) => d.dealId)]),
    ]);
  }

  const benchKeys = narviSticks ? benchKeysFor(narviSticks) : [];

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 720 }}
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
              <div className="toolbar-group" style={{ marginBottom: 8 }}>
                <span className="toolbar-label">Deals:</span>
                <input
                  type="text"
                  placeholder="filter, e.g. vault"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  style={{ width: 160 }}
                />
                <button
                  type="button"
                  className="tb-btn"
                  onClick={selectAllShown}
                  disabled={shown.length === 0}
                  title="Select every deal matching the filter"
                >
                  select all shown ({shown.length})
                </button>
                <button
                  type="button"
                  className="tb-btn"
                  onClick={() => setNarviDealIds([])}
                  disabled={narviDealIds.length === 0}
                >
                  clear
                </button>
                <label className="chk-inline" title="Show / hide the dashed planned sticks">
                  <input
                    type="checkbox"
                    checked={showNarviSticks}
                    disabled={narviDealIds.length === 0}
                    onChange={(e) => setShowNarviSticks(e.target.checked)}
                  />
                  Show on map
                </label>
              </div>

              <div
                style={{
                  maxHeight: 180,
                  overflowY: "auto",
                  border: "1px solid #e5e7eb",
                  borderRadius: 6,
                  padding: "6px 10px",
                  marginBottom: 12,
                }}
              >
                {shown.length === 0 && (
                  <p className="muted" style={{ fontSize: 11, margin: 0 }}>
                    no narvi deals match “{filter}”
                  </p>
                )}
                {shown.map((d) => (
                  <label
                    key={d.dealId}
                    className="chk-inline"
                    style={{ display: "flex", alignItems: "center", gap: 6 }}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(d.dealId)}
                      onChange={(e) => toggleDeal(d.dealId, e.target.checked)}
                    />
                    {d.dealId}
                    <span className="muted" style={{ fontSize: 11 }}>
                      {d.wells} wells
                      {d.scenarios > 1 ? ` · ${d.scenarios} scenarios` : ""}
                    </span>
                  </label>
                ))}
              </div>

              {narviDealIds.length > 0 && (
                <>
                  <p className="muted" style={{ fontSize: 11, marginTop: 0 }}>
                    {narviDealIds.length} deal
                    {narviDealIds.length === 1 ? "" : "s"} selected. Planned
                    sticks only (PUD / UPSIDE) — existing producers are the
                    map&apos;s own solid wellsticks. Bench toggles below span
                    every selected deal and affect the dashed sticks only, not
                    the PDP formation filters.
                  </p>
                  {narviSticks && narviSticks.missing_deal_ids.length > 0 && (
                    <div className="alert alert-error" style={{ marginBottom: 8 }}>
                      not found in narvi:{" "}
                      {narviSticks.missing_deal_ids.join(", ")}
                    </div>
                  )}

                  <div style={{ marginBottom: 8 }}>
                    <strong style={{ fontSize: 12 }}>
                      Benches
                      {narviSticks ? ` (${narviSticks.wells.length} planned wells)` : ""}
                    </strong>
                    {!narviSticks && (
                      <p className="muted" style={{ fontSize: 11 }}>
                        {showNarviSticks
                          ? "loading sticks…"
                          : "enable “Show on map” to load benches"}
                      </p>
                    )}
                    {narviSticks && benchKeys.length === 0 && (
                      <p className="muted" style={{ fontSize: 11 }}>
                        no planned sticks in the selected deals
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
                          title="Show / hide this bench's planned sticks across all selected deals"
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
