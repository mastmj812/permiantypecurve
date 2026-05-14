import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchFacets, fetchOperators } from "../api/wells";
import type { WellStatus } from "../api/types";
import { groupedFormations } from "../map/formations";
import { useMapStore } from "../store/mapStore";

const ALL_STATUSES: WellStatus[] = ["PDP", "PA", "SI", "TA", "INACTIVE", "UNKNOWN"];

export function FilterPanel() {
  const filters = useMapStore((s) => s.filters);
  const setFormations = useMapStore((s) => s.setFormations);
  const setOperators = useMapStore((s) => s.setOperators);
  const setStatuses = useMapStore((s) => s.setStatuses);
  const setVintageRange = useMapStore((s) => s.setVintageRange);
  const setLateralRange = useMapStore((s) => s.setLateralRange);
  const resetFilters = useMapStore((s) => s.resetFilters);

  const facetsQ = useQuery({ queryKey: ["facets"], queryFn: fetchFacets });

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
        availableNames={facetsQ.data?.formations.map((f) => f.value) ?? []}
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
    </aside>
  );
}

// ---------------- Formation (grouped checkboxes) ----------------
function FormationSection({
  selected,
  availableNames,
  onChange,
}: {
  selected: string[];
  availableNames: string[];
  onChange: (next: string[]) => void;
}) {
  const groups = useMemo(() => groupedFormations(), []);
  const have = new Set(availableNames);

  function toggle(name: string) {
    if (selected.includes(name)) onChange(selected.filter((x) => x !== name));
    else onChange([...selected, name]);
  }

  return (
    <section className="filter-section">
      <h3>Formation</h3>
      {(["Wolfcamp", "Bone Spring", "Spraberry"] as const).map((g) => (
        <fieldset key={g} className="formation-group">
          <legend>{g}</legend>
          {groups[g].map((f) => {
            const present = have.size === 0 || have.has(f.name);
            return (
              <label
                key={f.name}
                className={`chk ${present ? "" : "chk-faded"}`}
                title={present ? "" : "no wells with this formation in the DB"}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(f.name)}
                  onChange={() => toggle(f.name)}
                />
                <span className="swatch" style={{ background: f.color }} />
                {f.name}
              </label>
            );
          })}
        </fieldset>
      ))}
    </section>
  );
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
