// Shared table components for any per-well forecast table: sortable
// header + stat row. Lives here (rather than buried in ReviewPage.tsx)
// so the TC workspace page can use the same look + sort behavior
// without copy-paste. Pure (non-component) helpers — decline/format/
// median/index — live in reviewTableHelpers.ts so this module can
// export components only (react-refresh/only-export-components).

import type { ReactNode } from "react";

export type SortDir = "asc" | "desc";

/** Sortable table header. Shows ▲/▼ on the active column. */
export function Th<K extends string>({
  k,
  sortKey,
  sortDir,
  onClick,
  children,
}: {
  k: K;
  sortKey: K;
  sortDir: SortDir;
  onClick: (k: K) => void;
  children: ReactNode;
}) {
  const active = k === sortKey;
  return (
    <th className={`sortable ${active ? "active" : ""}`} onClick={() => onClick(k)}>
      {children}
      {active && <span className="sort-arrow">{sortDir === "asc" ? "▲" : "▼"}</span>}
    </th>
  );
}

/** Label / value stat row used in the Review summary panel. */
export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}
