// Blue Ox curve-drop configuration for one deal — the assignment
// surface: one anduin deal = one Blue Ox codename = one governing
// workbook. The engineer picks the zones (type curves — cross-deal
// allowed), maps narvi benches into them, selects the narvi scenarios
// whose inventory feeds the drop, and sets the kickoff params. Save
// persists to deals.blueox_config (narvi selections are pinned
// server-side for staleness detection); Export runs the SAVED config.

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import {
  type BlueOxCategory,
  type BlueOxConfig,
  type BlueOxLevel,
  type BlueOxScenarioRef,
  type BlueOxScenarioStatus,
  BlueOxStaleError,
  type DealSummary,
  type NarviScenario,
  downloadBlueOxExport,
  getBlueOxConfig,
  listNarviScenarios,
  putBlueOxConfig,
} from "../api/deals";
import { type TypeCurveSummary, listTypeCurves } from "../api/typeCurves";

const ALL_LEVELS: BlueOxLevel[] = ["P10", "P25", "P75", "P90"];

// Map key for a (deal_id, scenario_id) pair. Both are dealIdFor-style
// slugs ([a-z0-9_]), so "/" cannot collide — and it matches how the
// pair is displayed everywhere else.
const pairKey = (dealId: string, scenarioId: string) => `${dealId}/${scenarioId}`;

const sameRef = (a: BlueOxScenarioRef, b: BlueOxScenarioRef) =>
  a.deal_id === b.deal_id && a.scenario_id === b.scenario_id;

// Does this zone's scope cover the scenario? Empty/absent scope = ALL
// selected scenarios (the legacy behavior). Mirrors the backend's
// routing semantics in _fetch_narvi_by_zone.
const scopeCovers = (
  scope: BlueOxScenarioRef[] | null | undefined,
  ref: BlueOxScenarioRef,
) => !scope || scope.length === 0 || scope.some((r) => sameRef(r, ref));

// "PUD WCA_1×3, WCA_2×2 · UPSIDE 3BSSD×4 · PDP×12" — what a scenario
// actually contains, so the zone categories and bench list can be
// eyeballed against it before export.
function breakdownText(s: NarviScenario): string {
  const parts: string[] = [];
  for (const cat of ["PUD", "UPSIDE"] as const) {
    const cells = s.breakdown.filter((b) => b.category === cat);
    if (cells.length > 0) {
      parts.push(
        `${cat} ${cells.map((b) => `${b.formation ?? "(no bench)"}×${b.n}`).join(", ")}`,
      );
    }
  }
  const pdp = s.breakdown
    .filter((b) => b.category === "PDP")
    .reduce((acc, b) => acc + b.n, 0);
  if (pdp > 0) parts.push(`PDP×${pdp}`);
  if (parts.length === 0) return `${s.total_wells ?? "?"} wells`;
  return parts.join(" · ");
}

function emptyConfig(dealName: string): BlueOxConfig {
  return {
    codename: dealName.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, ""),
    curve_months: 360,
    levels: [...ALL_LEVELS],
    prepared_by: "",
    zones: [],
    narvi_selections: [],
    exclude_benches: [],
  };
}

interface Props {
  deal: DealSummary;
  onClose: () => void;
}

export function BlueOxDropModal({ deal, onClose }: Props) {
  const [cfg, setCfg] = useState<BlueOxConfig | null>(null);
  const [narviStatus, setNarviStatus] = useState<BlueOxScenarioStatus[]>([]);
  const [curves, setCurves] = useState<TypeCurveSummary[]>([]);
  const [scenarios, setScenarios] = useState<NarviScenario[]>([]);
  const [scenariosLoaded, setScenariosLoaded] = useState(false);
  const [scenarioError, setScenarioError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"load" | "save" | "export" | null>("load");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // Zone index whose scenario-scope picker is expanded (null = none).
  const [scopeOpen, setScopeOpen] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const [cfgResp, curveList] = await Promise.all([
          getBlueOxConfig(deal.id),
          listTypeCurves(),
        ]);
        if (!alive) return;
        setCfg(cfgResp.config ?? emptyConfig(deal.name));
        setNarviStatus(cfgResp.narvi_status);
        setCurves(curveList);
      } catch (e) {
        if (alive) setError(String(e));
      } finally {
        if (alive) setBusy(null);
      }
      // narvi listing is a warehouse round-trip — load it separately so
      // a slow/unreachable warehouse doesn't block the config form.
      try {
        const s = await listNarviScenarios();
        if (alive) {
          setScenarios(s);
          setScenariosLoaded(true);
        }
      } catch (e) {
        if (alive) setScenarioError(String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [deal.id, deal.name]);

  const staleByKey = useMemo(() => {
    const m = new Map<string, BlueOxScenarioStatus>();
    for (const s of narviStatus) m.set(pairKey(s.deal_id, s.scenario_id), s);
    return m;
  }, [narviStatus]);

  const curveById = useMemo(() => {
    const m = new Map<string, TypeCurveSummary>();
    for (const c of curves) m.set(c.id, c);
    return m;
  }, [curves]);

  if (!cfg) {
    return createPortal(
      <div className="modal-backdrop" onClick={onClose}>
        <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 980 }}>
          <header className="modal-header">
            <strong>Blue Ox drop — {deal.name}</strong>
            <button type="button" className="link-btn" onClick={onClose}>close</button>
          </header>
          <div className="modal-body">
            {error ? <div className="alert alert-error">{error}</div> : <p className="muted">loading…</p>}
          </div>
        </div>
      </div>,
      document.body,
    );
  }

  const edit = (patch: Partial<BlueOxConfig>) => {
    setCfg({ ...cfg, ...patch });
    setDirty(true);
    setNotice(null);
  };

  const onSave = async () => {
    setBusy("save");
    setError(null);
    try {
      const resp = await putBlueOxConfig(deal.id, cfg);
      setCfg(resp.config ?? cfg);
      setNarviStatus(resp.narvi_status);
      setDirty(false);
      setNotice("config saved — narvi selections pinned");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const onExport = async (allowStale = false) => {
    setBusy("export");
    setError(null);
    try {
      const filename = await downloadBlueOxExport(deal.id, { allowStale });
      setNotice(`exported ${filename}`);
    } catch (e) {
      if (e instanceof BlueOxStaleError) {
        const go = window.confirm(
          `${e.message}\n\nExport anyway with the CURRENT narvi inventory?`,
        );
        if (go) {
          await onExport(true);
          return;
        }
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(null);
    }
  };

  const toggleLevel = (lv: BlueOxLevel) =>
    edit({
      levels: cfg.levels.includes(lv)
        ? cfg.levels.filter((x) => x !== lv)
        : [...ALL_LEVELS.filter((x) => cfg.levels.includes(x) || x === lv)],
    });

  // Default each zone's category to the declaration carried by its
  // narvi wells: majority PUD vs UPSIDE across the zone's benches in
  // the selected scenarios COVERED BY ITS SCOPE (tie -> PUD). Runs on
  // scenario/bench/scope edits only — a manual dropdown pick sticks
  // until the data changes.
  const deriveZoneCategories = (next: BlueOxConfig): BlueOxConfig => {
    const selected = scenarios.filter((s) =>
      next.narvi_selections.some(
        (x) => x.deal_id === s.deal_id && x.scenario_id === s.scenario_id,
      ),
    );
    if (selected.length === 0) return next;
    const zones = next.zones.map((z) => {
      let pud = 0;
      let upside = 0;
      for (const s of selected) {
        if (!scopeCovers(z.scenario_scope, s)) continue;
        for (const b of s.breakdown) {
          if (!b.formation || !z.benches.includes(b.formation)) continue;
          if (b.category === "PUD") pud += b.n;
          else if (b.category === "UPSIDE") upside += b.n;
        }
      }
      if (pud + upside === 0) return z; // no signal — leave as picked
      const cat: BlueOxCategory = pud >= upside ? "PUD" : "UPSIDE";
      return cat === z.reserve_category ? z : { ...z, reserve_category: cat };
    });
    return { ...next, zones };
  };

  // Planned-well count a zone captures under its current benches +
  // scope — the pre-export sanity number for a west/east split.
  const zoneWellCount = (z: BlueOxConfig["zones"][number]): number => {
    let n = 0;
    for (const s of scenarios) {
      const isSelected = cfg?.narvi_selections.some(
        (x) => x.deal_id === s.deal_id && x.scenario_id === s.scenario_id,
      );
      if (!isSelected || !scopeCovers(z.scenario_scope, s)) continue;
      for (const b of s.breakdown) {
        if (b.category === "PDP") continue;
        if (b.formation && z.benches.includes(b.formation)) n += b.n;
      }
    }
    return n;
  };

  // Pair-shaped so PHANTOM selections (pinned pairs whose narvi
  // scenario no longer exists) can be untoggled too — they have no
  // NarviScenario row to pass.
  const toggleScenario = (s: Pick<NarviScenario, "deal_id" | "scenario_id">) => {
    const has = cfg.narvi_selections.some(
      (x) => x.deal_id === s.deal_id && x.scenario_id === s.scenario_id,
    );
    edit(
      deriveZoneCategories({
        ...cfg,
        narvi_selections: has
          ? cfg.narvi_selections.filter(
              (x) => !(x.deal_id === s.deal_id && x.scenario_id === s.scenario_id),
            )
          : [...cfg.narvi_selections, { deal_id: s.deal_id, scenario_id: s.scenario_id }],
      }),
    );
  };

  // Pinned selections whose (deal_id, scenario_id) matches no current
  // narvi scenario — the scenario was deleted or re-saved under a
  // different deal. They'd otherwise be INVISIBLE (the list renders
  // only live rows) while still failing every config save with
  // "narvi scenarios not found". Rendered as uncheckable-warning rows
  // below so the engineer can drop them. Gated on scenariosLoaded: an
  // unloaded/unreachable list proves nothing missing.
  const phantoms = scenariosLoaded
    ? cfg.narvi_selections.filter(
        (x) =>
          !scenarios.some(
            (s) => s.deal_id === x.deal_id && s.scenario_id === x.scenario_id,
          ),
      )
    : [];

  const setZone = (i: number, patch: Partial<BlueOxConfig["zones"][number]>) =>
    edit({ zones: cfg.zones.map((z, j) => (j === i ? { ...z, ...patch } : z)) });

  // Toggle one scenario in a zone's scope. An UNSCOPED zone implicitly
  // covers every selection, so unchecking a box there materializes
  // "all minus this one" (not "only this one"). A scope that grows back
  // to the full selection set — or empties — stores null (= all), so
  // the config round-trips the legacy shape.
  const toggleZoneScope = (zi: number, ref: BlueOxScenarioRef) => {
    const z = cfg.zones[zi];
    if (!z) return;
    const cur: BlueOxScenarioRef[] = z.scenario_scope?.length
      ? z.scenario_scope
      : cfg.narvi_selections.map((s) => ({
          deal_id: s.deal_id,
          scenario_id: s.scenario_id,
        }));
    const nextScope = cur.some((r) => sameRef(r, ref))
      ? cur.filter((r) => !sameRef(r, ref))
      : [...cur, { deal_id: ref.deal_id, scenario_id: ref.scenario_id }];
    const coversAll =
      nextScope.length === cfg.narvi_selections.length || nextScope.length === 0;
    edit(
      deriveZoneCategories({
        ...cfg,
        zones: cfg.zones.map((zz, j) =>
          j === zi ? { ...zz, scenario_scope: coversAll ? null : nextScope } : zz,
        ),
      }),
    );
  };

  const setZoneBenches = (i: number, benches: string[]) =>
    edit(
      deriveZoneCategories({
        ...cfg,
        zones: cfg.zones.map((z, j) => (j === i ? { ...z, benches } : z)),
      }),
    );

  return createPortal(
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 980 }}>
        <header className="modal-header">
          <strong>Blue Ox drop — {deal.name}</strong>
          <button type="button" className="link-btn" onClick={onClose}>close</button>
        </header>
        <div className="modal-body" style={{ maxHeight: "72vh", overflowY: "auto" }}>
          {error && <div className="alert alert-error" style={{ marginBottom: 8 }}>{error}</div>}
          {notice && !error && (
            <div className="alert" style={{ marginBottom: 8 }}>{notice}</div>
          )}

          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
            <label style={{ fontSize: 12 }}>
              codename{" "}
              <input value={cfg.codename} size={18}
                onChange={(e) => edit({ codename: e.target.value })} />
            </label>
            <label style={{ fontSize: 12 }}>
              curve months{" "}
              <input type="number" value={cfg.curve_months} min={12} max={600} step={12}
                style={{ width: 70 }}
                onChange={(e) => edit({ curve_months: Number(e.target.value) })} />
            </label>
            <span style={{ fontSize: 12 }}>
              levels{" "}
              {ALL_LEVELS.map((lv) => (
                <label key={lv} style={{ marginRight: 6 }}>
                  <input type="checkbox" checked={cfg.levels.includes(lv)}
                    onChange={() => toggleLevel(lv)} /> {lv}
                </label>
              ))}
            </span>
            <label style={{ fontSize: 12 }}>
              prepared by{" "}
              <input value={cfg.prepared_by} size={12}
                onChange={(e) => edit({ prepared_by: e.target.value })} />
            </label>
          </div>

          <h4 style={{ margin: "10px 0 4px" }}>Zones</h4>
          <p className="muted" style={{ fontSize: 11, margin: "0 0 6px" }}>
            Zone names ship to Blue Ox verbatim (≤26 chars, stable across re-drops).
            Curves may come from any deal. Benches = narvi formation codes mapped into
            the zone (comma-separated, e.g. WCA_1, WCA_2).
          </p>
          <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left" }}>
                <th>type curve</th><th>zone name</th><th>category</th><th>benches</th>
                <th title="which selected narvi scenarios (DSUs) this zone captures — lets the same bench carry different curves in different areas">
                  scenarios
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              {cfg.zones.map((z, i) => {
                const curve = curveById.get(z.type_curve_id);
                const crossDeal = curve && curve.deal_id !== deal.id;
                return (
                  <tr key={i}>
                    <td style={{ paddingRight: 8 }}>
                      <select value={z.type_curve_id}
                        onChange={(e) => {
                          const c = curveById.get(e.target.value);
                          setZone(i, {
                            type_curve_id: e.target.value,
                            zone_name: z.zone_name ?? (c ? c.name.slice(0, 26) : null),
                          });
                        }}>
                        {curves.map((c) => (
                          <option key={c.id} value={c.id}>{c.name}</option>
                        ))}
                      </select>
                      {crossDeal && (
                        <span className="muted" title="curve assigned to another deal — allowed"
                          style={{ marginLeft: 4, fontSize: 10 }}>
                          ↗ cross-deal
                        </span>
                      )}
                    </td>
                    <td style={{ paddingRight: 8 }}>
                      <input value={z.zone_name ?? ""} size={20} maxLength={26}
                        placeholder={curve?.name.slice(0, 26) ?? ""}
                        onChange={(e) => setZone(i, { zone_name: e.target.value || null })} />
                    </td>
                    <td style={{ paddingRight: 8 }}>
                      <select value={z.reserve_category}
                        title="defaults to the declaration on the zone's narvi wells — override sticks until benches/scenarios change"
                        onChange={(e) =>
                          setZone(i, { reserve_category: e.target.value as BlueOxCategory })}>
                        <option value="PUD">PUD</option>
                        <option value="UPSIDE">UPSIDE</option>
                      </select>
                    </td>
                    <td style={{ paddingRight: 8 }}>
                      <input value={z.benches.join(", ")} size={22}
                        placeholder="WCA_1, WCA_2"
                        onChange={(e) => setZoneBenches(
                          i,
                          e.target.value.split(",").map((b) => b.trim()).filter(Boolean),
                        )} />
                    </td>
                    <td style={{ paddingRight: 8, whiteSpace: "nowrap" }}>
                      <button
                        type="button" className="link-btn"
                        title="pick which selected scenarios this zone captures"
                        onClick={() => setScopeOpen(scopeOpen === i ? null : i)}
                      >
                        {!z.scenario_scope?.length
                          ? `all (${cfg.narvi_selections.length})`
                          : `${z.scenario_scope.length} of ${cfg.narvi_selections.length}`}
                        {scopeOpen === i ? " ▴" : " ▾"}
                      </button>
                      <span className="muted"> · {zoneWellCount(z)}w</span>
                    </td>
                    <td>
                      <button type="button" className="link-btn" style={{ color: "#dc2626" }}
                        onClick={() => edit({ zones: cfg.zones.filter((_, j) => j !== i) })}>
                        remove
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {scopeOpen != null && cfg.zones[scopeOpen] && (
            <div
              style={{
                margin: "4px 0 6px", padding: "6px 8px", fontSize: 12,
                border: "1px solid var(--line, #e5e7eb)", borderRadius: 5,
              }}
            >
              <div style={{ marginBottom: 4 }}>
                <strong>
                  {cfg.zones[scopeOpen].zone_name ??
                    curveById.get(cfg.zones[scopeOpen].type_curve_id)?.name}
                </strong>
                <span className="muted"> — scenarios this zone captures.</span>{" "}
                <button
                  type="button" className="link-btn"
                  disabled={!cfg.zones[scopeOpen].scenario_scope?.length}
                  onClick={() =>
                    edit(
                      deriveZoneCategories({
                        ...cfg,
                        zones: cfg.zones.map((zz, j) =>
                          j === scopeOpen ? { ...zz, scenario_scope: null } : zz,
                        ),
                      }),
                    )
                  }
                >
                  all scenarios
                </button>
              </div>
              {cfg.narvi_selections.map((sel) => {
                const zi = scopeOpen;
                const z = cfg.zones[zi];
                if (!z) return null;
                const meta = scenarios.find(
                  (s) => s.deal_id === sel.deal_id && s.scenario_id === sel.scenario_id,
                );
                const scoped = !!z.scenario_scope?.some((r) => sameRef(r, sel));
                return (
                  <label
                    key={pairKey(sel.deal_id, sel.scenario_id)}
                    style={{ display: "block", marginBottom: 2 }}
                    title={pairKey(sel.deal_id, sel.scenario_id)}
                  >
                    <input
                      type="checkbox"
                      // unscoped zone = every box effectively on
                      checked={!z.scenario_scope?.length || scoped}
                      onChange={() => toggleZoneScope(zi, sel)}
                    />{" "}
                    {meta?.name ?? sel.scenario_id}
                  </label>
                );
              })}
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 11 }}>
                unchecking a scenario narrows this zone to the ones still
                checked; the same bench may then carry a different curve in
                another zone scoped to the other scenarios (west/east splits).
                A scenario left uncovered by every zone blocks the export.
              </p>
            </div>
          )}
          <button type="button" className="tb-btn" style={{ marginTop: 4 }}
            disabled={curves.length === 0}
            onClick={() => {
              const first = curves[0];
              if (!first) return;
              edit({
                zones: [...cfg.zones, {
                  type_curve_id: first.id,
                  zone_name: first.name.slice(0, 26),
                  reserve_category: "PUD",
                  benches: [],
                }],
              });
            }}>
            + add zone
          </button>

          <h4 style={{ margin: "14px 0 4px" }}>narvi scenarios</h4>
          {scenarioError && (
            <div className="alert alert-error" style={{ fontSize: 11 }}>{scenarioError}</div>
          )}
          {scenarios.length === 0 && !scenarioError && (
            <p className="muted" style={{ fontSize: 11 }}>loading scenario list…</p>
          )}
          <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: 12 }}>
            {scenarios.map((s) => {
              const checked = cfg.narvi_selections.some(
                (x) => x.deal_id === s.deal_id && x.scenario_id === s.scenario_id,
              );
              const status = staleByKey.get(pairKey(s.deal_id, s.scenario_id));
              return (
                <li key={`${s.deal_id}/${s.scenario_id}`} style={{ marginBottom: 2 }}>
                  <label
                    title={`${s.deal_id}/${s.scenario_id} · saved ${s.updated_at.slice(0, 16).replace("T", " ")}`}>
                    <input type="checkbox" checked={checked} onChange={() => toggleScenario(s)} />
                    {" "}<b>{s.name ?? s.scenario_id}</b>
                    <span className="muted"> · {breakdownText(s)}</span>
                    {status?.stale && (
                      <span style={{ color: "#b45309", fontWeight: 600 }}>
                        {" "}· changed since pin — re-save config
                      </span>
                    )}
                  </label>
                </li>
              );
            })}
            {phantoms.map((x) => (
              <li key={`${x.deal_id}/${x.scenario_id}`} style={{ marginBottom: 2 }}>
                <label
                  title="pinned in this config, but no narvi scenario has this deal/scenario id anymore (deleted, or re-saved under a different deal)">
                  <input type="checkbox" checked onChange={() => toggleScenario(x)} />
                  {" "}<b style={{ color: "#dc2626" }}>{x.deal_id}/{x.scenario_id}</b>
                  <span style={{ color: "#dc2626", fontWeight: 600 }}>
                    {" "}· not found in narvi — uncheck to drop it from this config
                  </span>
                </label>
              </li>
            ))}
          </ul>

          <h4 style={{ margin: "14px 0 4px" }}>excluded benches</h4>
          <input value={cfg.exclude_benches.join(", ")} size={40}
            placeholder="benches whose PLANNED wells are carried in a sibling drop"
            onChange={(e) => edit({
              exclude_benches: e.target.value.split(",").map((b) => b.trim()).filter(Boolean),
            })} />
        </div>
        <footer style={{ display: "flex", gap: 8, alignItems: "center", padding: "10px 16px", borderTop: "1px solid var(--border, #e5e7eb)" }}>
          <button type="button" className="tb-btn" disabled={busy !== null}
            onClick={() => void onSave()}>
            {busy === "save" ? "saving…" : "Save config"}
          </button>
          <button type="button" className="tb-btn" disabled={busy !== null || dirty}
            title={dirty ? "save the config first — export runs the SAVED config" : "cut the governing workbook from the saved config"}
            onClick={() => void onExport()}>
            {busy === "export" ? "exporting…" : "Export drop .xlsx"}
          </button>
          <button type="button" className="tb-btn" disabled={busy !== null || dirty}
            title={dirty
              ? "save the config first — the dossier renders the SAVED config"
              : "open the dossier preview (scenario maps + gunbarrels + type curves); export the .pptx from there"}
            onClick={() => window.open(`#/deals/${deal.id}/dossier`, "_blank")}>
            Dossier preview
          </button>
          {dirty && <span className="muted" style={{ fontSize: 11 }}>unsaved changes</span>}
        </footer>
      </div>
    </div>,
    document.body,
  );
}
