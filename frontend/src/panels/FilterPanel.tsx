import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { fetchFacets, fetchOperators } from "../api/wells";
import type { FacetCount, WellStatus } from "../api/types";
import { type FormationGroup, colorForFormation } from "../map/formations";
import { useMapStore } from "../store/mapStore";

const ALL_STATUSES: WellStatus[] = ["PDP", "PA", "SI", "TA", "INACTIVE", "UNKNOWN"];

export function FilterPanel() {
  const filters = useMapStore((s) => s.filters);
  const setFormations = useMapStore((s) => s.setFormations);
  const setOperators = useMapStore((s) => s.setOperators);
  const setStatuses = useMapStore((s) => s.setStatuses);
  const setVintageRange = useMapStore((s) => s.setVintageRange);
  const setLateralRange = useMapStore((s) => s.setLateralRange);
  const setApi10s = useMapStore((s) => s.setApi10s);
  const resetFilters = useMapStore((s) => s.resetFilters);

  // Refetch facets whenever filters change so per-facet counts respond
  // ("WOLFCAMP A has 12 wells under your current vintage window").
  // The backend excludes the facet's own column from its own count.
  //
  // keepPreviousData holds the prior facet list visible during the
  // in-flight refetch. Without it, `data` flips to undefined on every
  // filter change, the FormationSection collapses to four empty
  // fieldsets, and the panel layout jumps up — which makes typing
  // into the date inputs feel like the panel is scrolling on every
  // keystroke. The new facets land a moment later and the list
  // re-expands, but the jitter is the actual UX complaint.
  const facetsQ = useQuery({
    queryKey: ["facets", filters],
    queryFn: () => fetchFacets(filters),
    placeholderData: keepPreviousData,
  });

  return (
    <aside className="panel panel-left">
      <header className="panel-header">
        <span>Filters</span>
        <button className="link-btn" type="button" onClick={resetFilters}>
          reset
        </button>
      </header>

      <FormationSection
        selected={filters.formations}
        facets={facetsQ.data?.formations ?? []}
        onChange={setFormations}
      />

      <VintageSection
        start={filters.first_prod_start}
        end={filters.first_prod_end}
        onChange={setVintageRange}
      />

      <OperatorSection selected={filters.operators} onChange={setOperators} />

      <LateralSection
        min={filters.lateral_min_ft}
        max={filters.lateral_max_ft}
        bounds={[
          facetsQ.data?.lateral_ft_min ?? null,
          facetsQ.data?.lateral_ft_max ?? null,
        ]}
        onChange={setLateralRange}
      />

      <StatusSection selected={filters.statuses} onChange={setStatuses} />

      <Api10Section selected={filters.api10s} onChange={setApi10s} />
    </aside>
  );
}

// ---------------- API10 paste filter ----------------
// Paste a multiline list of API10s from another tool's well-list export.
// When non-empty, the map is filtered to ONLY those wells (intersected
// with the other active filters). Empty textarea = no filter.
function Api10Section({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  // Local draft state — only commits to the store on blur / Apply, so
  // we don't refetch tiles on every keystroke during a multi-thousand-
  // character paste.
  const [draft, setDraft] = useState<string>(() => selected.join("\n"));

  // If the store value changes from outside (e.g. resetFilters), pull
  // it back into the textarea.
  useEffect(() => {
    setDraft(selected.join("\n"));
  }, [selected.join(",")]);

  // Extract every 10-digit run (Novi wellbore identifier), regardless
  // of separator. Tolerates dashes (e.g. "42-301-36707" = 10 digits
  // when stripped), commas, tabs, newlines, and leading/trailing junk.
  // Pasting full 14-digit api14s still works — the regex consumes the
  // leading 10 digits, which is the api10 of the same wellbore.
  const parsed = useMemo(() => extractApi10s(draft), [draft]);
  const dirty = !arraysEqual(parsed, selected);

  return (
    <section className="filter-section">
      <h3>API10 list</h3>
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="paste api10s (one per line or comma-separated)"
        rows={5}
        spellCheck={false}
        style={{ width: "100%", fontFamily: "monospace", fontSize: 11 }}
      />
      <div
        className="muted"
        style={{ fontSize: 11, marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap" }}
      >
        {parsed.length === 0
          ? <span>no api10s parsed</span>
          : <span>{parsed.length} api10{parsed.length === 1 ? "" : "s"} parsed</span>}
        {parsed.length > 500 && (
          <span style={{ color: "#dc2626" }}>
            — over 500 will likely overflow the tile URL
          </span>
        )}
      </div>
      <div className="param-actions" style={{ marginTop: 4 }}>
        <button
          type="button"
          className="btn-primary"
          onClick={() => onChange(parsed)}
          disabled={!dirty}
          title={dirty ? "Apply this list as the filter" : "List is already applied"}
        >
          apply
        </button>
        <button
          type="button"
          className="tb-btn"
          onClick={() => {
            setDraft("");
            onChange([]);
          }}
          disabled={selected.length === 0 && draft === ""}
        >
          clear
        </button>
      </div>
    </section>
  );
}

const API10_RE = /\d{10}/g;

function extractApi10s(text: string): string[] {
  // Strip dashes so "42-301-36707" → "4230136707" before matching.
  const compact = text.replace(/-/g, "");
  const matches = compact.match(API10_RE) ?? [];
  // De-dupe while preserving paste order so the user can tell which
  // entries collapsed if they expected a different count.
  const seen = new Set<string>();
  const out: string[] = [];
  for (const m of matches) {
    if (!seen.has(m)) {
      seen.add(m);
      out.push(m);
    }
  }
  return out;
}

function arraysEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// ---------------- Formation (grouped checkboxes) ----------------
// Data-driven: render exactly the formations that exist in the DB,
// grouped by name prefix. Faded when count === 0 under the current
// filters (the formation has wells in the DB, but none match the
// active vintage / lateral / status / etc).
function FormationSection({
  selected,
  facets,
  onChange,
}: {
  selected: string[];
  facets: FacetCount[];
  onChange: (next: string[]) => void;
}) {
  // Group by name prefix. Enverus is inconsistent about subdivision —
  // we just classify by the leading token so new variants land in the
  // right section automatically.
  const groups = useMemo(() => {
    const out: Record<FormationGroup, FacetCount[]> = {
      Wolfcamp: [],
      "Bone Spring": [],
      Spraberry: [],
      Other: [],
    };
    for (const f of facets) {
      out[groupForName(f.value)].push(f);
    }
    // Inside each group, sort by count desc then name asc so the bulk
    // formations bubble up.
    for (const k of Object.keys(out) as FormationGroup[]) {
      out[k].sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
    }
    return out;
  }, [facets]);

  function toggle(name: string) {
    if (selected.includes(name)) onChange(selected.filter((x) => x !== name));
    else onChange([...selected, name]);
  }

  // Render groups in fixed order, skipping empty ones. "Other" only
  // appears when there's something unclassified in the data.
  const order: FormationGroup[] = ["Wolfcamp", "Bone Spring", "Spraberry", "Other"];

  return (
    <section className="filter-section">
      <h3>Formation</h3>
      {order.map((g) => {
        const items = groups[g];
        if (items.length === 0) return null;
        return (
          <fieldset key={g} className="formation-group">
            <legend>{g}</legend>
            {items.map((f) => {
              const matches = f.count > 0;
              return (
                <label
                  key={f.value}
                  className={`chk ${matches ? "" : "chk-faded"}`}
                  title={
                    matches
                      ? `${f.count} well${f.count === 1 ? "" : "s"} under current filters`
                      : "no wells match the current filters"
                  }
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(f.value)}
                    onChange={() => toggle(f.value)}
                  />
                  <span
                    className="swatch"
                    style={{ background: colorForFormation(f.value) }}
                  />
                  {f.value}
                  <span className="muted" style={{ marginLeft: 4 }}>
                    ({f.count})
                  </span>
                </label>
              );
            })}
          </fieldset>
        );
      })}
    </section>
  );
}

// Classify a formation name into a UI group by leading token. Case-
// insensitive to absorb Enverus inconsistency. Order matters: a name
// like "BONE SPRING,WOLFCAMP" mentions both intervals, but we group it
// under Bone Spring (the shallower one) since that's typically the
// primary producer in a dual-interval completion.
function groupForName(name: string): FormationGroup {
  const u = name.toUpperCase();
  if (u.includes("BONE SPRING") || u.includes("AVALON")) return "Bone Spring";
  // "WLFCMP" catches the abbreviated form Enverus occasionally uses
  // (e.g. "WLFCMP\\PENN CONS"). Without this, those rows fall through
  // to Other even though the operator clearly meant Wolfcamp.
  if (u.includes("WOLFCAMP") || u.includes("WLFCMP")) return "Wolfcamp";
  if (
    u.includes("SPRABERRY") ||
    u.includes("JO MILL") ||
    u.startsWith("DEAN")
  ) {
    return "Spraberry";
  }
  return "Other";
}

// ---------------- Vintage (date range) ----------------
function VintageSection({
  start,
  end,
  onChange,
}: {
  start: string | null;
  end: string | null;
  onChange: (start: string | null, end: string | null) => void;
}) {
  // Brief default: last 10 years. Apply once on first paint if both empty.
  useEffect(() => {
    if (start === null && end === null) {
      const today = new Date();
      const startDate = new Date(today.getFullYear() - 10, 0, 1);
      onChange(startDate.toISOString().slice(0, 10), today.toISOString().slice(0, 10));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section className="filter-section">
      <h3>First-prod date</h3>
      <div className="row">
        <input
          type="date"
          value={start ?? ""}
          onChange={(e) => onChange(e.target.value || null, end)}
        />
        <span className="row-sep">—</span>
        <input
          type="date"
          value={end ?? ""}
          onChange={(e) => onChange(start, e.target.value || null)}
        />
      </div>
    </section>
  );
}

// ---------------- Operator (type-ahead multi-select) ----------------
function OperatorSection({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 150);
    return () => clearTimeout(id);
  }, [q]);

  const opsQ = useQuery({
    queryKey: ["operators", debouncedQ],
    queryFn: () => fetchOperators(debouncedQ),
    staleTime: 60_000,
  });

  function add(name: string) {
    if (!selected.includes(name)) onChange([...selected, name]);
    setQ("");
  }
  function remove(name: string) {
    onChange(selected.filter((x) => x !== name));
  }

  return (
    <section className="filter-section">
      <h3>Operator</h3>
      <input
        type="search"
        placeholder="type to filter…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="chip-row">
        {selected.map((name) => (
          <span key={name} className="chip">
            {name}
            <button type="button" onClick={() => remove(name)} aria-label="remove">
              ×
            </button>
          </span>
        ))}
      </div>
      {q && opsQ.data && (
        <ul className="typeahead-list">
          {opsQ.data.slice(0, 10).map((m) => (
            <li key={m.operator}>
              <button type="button" onClick={() => add(m.operator)}>
                {m.operator} <span className="muted">({m.count})</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------- Lateral length (min/max sliders) ----------------
function LateralSection({
  min,
  max,
  bounds,
  onChange,
}: {
  min: number | null;
  max: number | null;
  bounds: [number | null, number | null];
  onChange: (min: number | null, max: number | null) => void;
}) {
  const lo = Math.floor((bounds[0] ?? 3000) / 500) * 500;
  const hi = Math.ceil((bounds[1] ?? 15000) / 500) * 500;

  return (
    <section className="filter-section">
      <h3>Lateral length (ft)</h3>
      <div className="row">
        <input
          type="number"
          min={lo}
          max={hi}
          step={500}
          placeholder={`${lo}`}
          value={min ?? ""}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null, max)}
        />
        <span className="row-sep">—</span>
        <input
          type="number"
          min={lo}
          max={hi}
          step={500}
          placeholder={`${hi}`}
          value={max ?? ""}
          onChange={(e) => onChange(min, e.target.value ? Number(e.target.value) : null)}
        />
      </div>
    </section>
  );
}

// ---------------- Status (checkboxes; default PDP only) ----------------
function StatusSection({
  selected,
  onChange,
}: {
  selected: WellStatus[];
  onChange: (next: WellStatus[]) => void;
}) {
  function toggle(s: WellStatus) {
    if (selected.includes(s)) onChange(selected.filter((x) => x !== s));
    else onChange([...selected, s]);
  }
  return (
    <section className="filter-section">
      <h3>Status</h3>
      <div className="chip-row">
        {ALL_STATUSES.map((s) => (
          <label key={s} className="chk-inline">
            <input
              type="checkbox"
              checked={selected.includes(s)}
              onChange={() => toggle(s)}
            />
            {s}
          </label>
        ))}
      </div>
    </section>
  );
}
