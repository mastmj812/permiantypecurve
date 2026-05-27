// Review page: sortable, filterable table of forecasted wells with
// outlier flagging on EUR-per-lateral-foot. Per-well checkbox to
// include/exclude from type-curve aggregation (step 6).

import { useEffect, useMemo, useState } from "react";

import {
  type ForecastRow,
  listForecasts,
  type Stream,
} from "../api/forecasts";
import { eurFromForecastParams } from "../forecasts/arps";
import { ForecastDetailModal } from "../forecasts/ForecastDetailModal";
import { computeOutliers } from "../forecasts/outliers";
import {
  Stat,
  Th,
  effDiFor,
  fmtDi,
  fmtInt,
  fmtVol,
  indexFitsByWellStream,
  median,
  type SortDir,
} from "../forecasts/reviewTable";
import { ReviewMap } from "../components/ReviewMap";
import { useMapStore } from "../store/mapStore";

type SortKey =
  | "api14"
  | "well_name"
  | "formation"
  | "operator"
  | "first_prod_date"
  | "lateral"
  | "eur"
  | "eur_per_ft"
  | "fit_r2"
  | "oil_di"
  | "gas_di"
  | "water_di"
  | "downtime";

export function ReviewPage() {
  const api14s = useMapStore((s) => s.forecastApi14s);
  const excluded = useMapStore((s) => s.excludedApi14s);
  const toggleExcluded = useMapStore((s) => s.toggleExcluded);
  const setCurrentPage = useMapStore((s) => s.setCurrentPage);

  const [allForecasts, setAllForecasts] = useState<ForecastRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("eur_per_ft");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [formationFilter, setFormationFilter] = useState<string>("all");
  const [showOnlyOutliers, setShowOnlyOutliers] = useState(false);
  const [openApi14, setOpenApi14] = useState<string | null>(null);

  useEffect(() => {
    if (api14s.length === 0) return;
    setLoading(true);
    listForecasts(api14s)
      .then(setAllForecasts)
      .finally(() => setLoading(false));
  }, [api14s.join(",")]);

  // Override each forecast's stored `eur` with a client-side recompute
  // from the persisted Arps params. The stored value was written at fit
  // time with the old 5-BOPD econ-limit cutoff baked in, which clipped
  // ~20% of the late-time tail. The Type Curve tab moved to the raw
  // 50-yr integral (no cutoff) — this brings every EUR shown on the
  // Review tab (table, EUR/ft sort, outlier stats, map-color ramp, the
  // per-well modal's stat row) onto the same convention without forcing
  // a re-fit of every saved forecast. Falls back to the stored value
  // when params are missing/non-finite.
  const allForecastsCorrected = useMemo(
    () =>
      allForecasts.map((f) => {
        const recomputed = eurFromForecastParams(f.params);
        return recomputed != null ? { ...f, eur: recomputed } : f;
      }),
    [allForecasts],
  );

  // Show one row per WELL (use the oil forecast — the brief frames type
  // curves as per-well, and gas/water inherit oil's peak).
  const oilRows = useMemo(
    () => allForecastsCorrected.filter((f) => f.stream === "oil"),
    [allForecastsCorrected],
  );

  // Per-(api14, stream) lookup so the Di columns can pull each well's
  // gas + water fits alongside oil without re-scanning the flat list
  // every row render.
  const fitsByWellStream = useMemo(
    () => indexFitsByWellStream(allForecastsCorrected),
    [allForecastsCorrected],
  );

  // Outliers + summary stats are computed from the currently-INCLUDED
  // wells, not the full set. That way the iterative review workflow
  // works: as the engineer excludes obvious outliers, the median /
  // σ / 2σ band shift and the next tier of marginal wells gets
  // highlighted for review. Toggling a checkbox updates the right-rail
  // stats and the row badges together.
  const outliers = useMemo(
    () =>
      computeOutliers(
        oilRows
          .filter((f) => !excluded.has(f.api14))
          .map((f) => ({
            api14: f.api14,
            eur: f.eur,
            lateral_ft: f.well_lateral_ft,
          })),
      ),
    [oilRows, excluded],
  );

  const formations = useMemo(() => {
    const s = new Set<string>();
    for (const r of oilRows) if (r.well_formation) s.add(r.well_formation);
    return ["all", ...Array.from(s).sort()];
  }, [oilRows]);

  const filtered = useMemo(() => {
    let rows = oilRows;
    if (formationFilter !== "all") {
      rows = rows.filter((r) => r.well_formation === formationFilter);
    }
    if (showOnlyOutliers) {
      rows = rows.filter((r) => outliers.outlierApi14s.has(r.api14));
    }
    return rows;
  }, [oilRows, formationFilter, showOnlyOutliers, outliers]);

  const sorted = useMemo(() => {
    const sign = sortDir === "asc" ? 1 : -1;
    const get = (r: ForecastRow): number | string => {
      switch (sortKey) {
        case "api14": return r.api14;
        case "well_name": return r.well_name ?? "";
        case "formation": return r.well_formation ?? "";
        case "operator": return r.well_operator ?? "";
        // ISO dates sort lexicographically (YYYY-MM-DD), so string
        // compare is correct here without a Date parse.
        case "first_prod_date": return r.well_first_prod_date ?? "";
        case "lateral": return r.well_lateral_ft ?? 0;
        case "eur": return r.eur ?? 0;
        case "eur_per_ft":
          return r.eur != null && r.well_lateral_ft
            ? r.eur / r.well_lateral_ft
            : 0;
        case "fit_r2": return r.fit_r2 ?? 0;
        case "oil_di":
          return effDiFor(fitsByWellStream.get(r.api14)?.get("oil")) ?? -1;
        case "gas_di":
          return effDiFor(fitsByWellStream.get(r.api14)?.get("gas")) ?? -1;
        case "water_di":
          return effDiFor(fitsByWellStream.get(r.api14)?.get("water")) ?? -1;
        case "downtime": return r.downtime_ratio ?? 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const av = get(a), bv = get(b);
      if (typeof av === "string") return sign * av.localeCompare(bv as string);
      return sign * ((av as number) - (bv as number));
    });
  }, [filtered, sortKey, sortDir, fitsByWellStream]);

  function clickHeader(key: SortKey) {
    if (key === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const includedCount = filtered.filter((r) => !excluded.has(r.api14)).length;
  const excludedCount = filtered.filter((r) => excluded.has(r.api14)).length;
  const outlierCount = filtered.filter((r) => outliers.outlierApi14s.has(r.api14)).length;

  // Cohort-level median 1-yr effective Di across the currently INCLUDED
  // wells (matches how the outlier stats / EUR-per-ft band are scoped).
  const diMedians = useMemo(() => {
    const included = oilRows.filter((r) => !excluded.has(r.api14));
    const collect = (stream: Stream): number[] =>
      included
        .map((r) => effDiFor(fitsByWellStream.get(r.api14)?.get(stream)))
        .filter((v): v is number => v != null && Number.isFinite(v));
    return {
      oil: median(collect("oil")),
      gas: median(collect("gas")),
      water: median(collect("water")),
    };
  }, [oilRows, excluded, fitsByWellStream]);

  return (
    <div className="page page-two-col">
      <div className="review-table-wrap">
        <header className="review-header">
          <strong>Review</strong>
          <span className="muted">{oilRows.length} wells in scope</span>
          <select
            value={formationFilter}
            onChange={(e) => setFormationFilter(e.target.value)}
          >
            {formations.map((f) => (
              <option key={f} value={f}>
                {f === "all" ? "all formations" : f}
              </option>
            ))}
          </select>
          <label className="chk-inline">
            <input
              type="checkbox"
              checked={showOnlyOutliers}
              onChange={(e) => setShowOnlyOutliers(e.target.checked)}
            />
            outliers only
          </label>
          {loading && <span className="muted">loading…</span>}
        </header>

        <div className="review-table">
          <table>
            <thead>
              <tr>
                <th>include</th>
                <Th k="api14" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  api14
                </Th>
                <Th k="well_name" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  well name
                </Th>
                <Th k="formation" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  formation
                </Th>
                <Th k="operator" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  operator
                </Th>
                <Th
                  k="first_prod_date"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onClick={clickHeader}
                >
                  First Prod Date
                </Th>
                <Th k="lateral" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  lateral (ft)
                </Th>
                <Th k="eur" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  EUR (BBL)
                </Th>
                <Th k="eur_per_ft" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  EUR / ft
                </Th>
                <Th k="fit_r2" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  R²
                </Th>
                <Th k="oil_di" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  Oil Di
                </Th>
                <Th k="gas_di" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  Gas Di
                </Th>
                <Th k="water_di" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  Water Di
                </Th>
                <Th k="downtime" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  downtime
                </Th>
                <th>flags</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const isOut = outliers.outlierApi14s.has(r.api14);
                const isExcluded = excluded.has(r.api14);
                const eurPerFt =
                  r.eur != null && r.well_lateral_ft
                    ? r.eur / r.well_lateral_ft
                    : null;
                return (
                  <tr
                    key={r.api14}
                    className={
                      [
                        "row-clickable",
                        isOut ? "row-outlier" : "",
                        isExcluded ? "row-excluded" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")
                    }
                    onClick={() => setOpenApi14(r.api14)}
                  >
                    {/* The include/exclude checkbox cell must NOT open
                        the modal — stop propagation on both the cell
                        and the input. */}
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={!isExcluded}
                        onChange={() => toggleExcluded(r.api14)}
                      />
                    </td>
                    <td>{r.api14}</td>
                    <td>{r.well_name ?? "—"}</td>
                    <td>{r.well_formation ?? "—"}</td>
                    <td>{r.well_operator ?? "—"}</td>
                    <td>{r.well_first_prod_date ?? "—"}</td>
                    <td>{fmtInt(r.well_lateral_ft)}</td>
                    <td>{fmtVol(r.eur)}</td>
                    <td>{eurPerFt != null ? eurPerFt.toFixed(1) : "—"}</td>
                    <td>{r.fit_r2 != null ? r.fit_r2.toFixed(3) : "—"}</td>
                    <td>{fmtDi(effDiFor(fitsByWellStream.get(r.api14)?.get("oil")))}</td>
                    <td>{fmtDi(effDiFor(fitsByWellStream.get(r.api14)?.get("gas")))}</td>
                    <td>{fmtDi(effDiFor(fitsByWellStream.get(r.api14)?.get("water")))}</td>
                    <td>
                      {r.downtime_ratio != null
                        ? `${(r.downtime_ratio * 100).toFixed(0)}%`
                        : "—"}
                    </td>
                    <td>
                      {isOut && <span className="badge badge-warn">outlier</span>}
                      {r.fit_at_bound && (
                        <span className="badge badge-warn">at bound</span>
                      )}
                      {r.downtime_ratio != null && r.downtime_ratio > 0.15 && (
                        <span
                          className="badge badge-warn"
                          title={`${(r.downtime_ratio * 100).toFixed(0)}% of post-peak months excluded as downtime`}
                        >
                          downtime
                        </span>
                      )}
                      {r.manual_override && (
                        <span className="badge badge-warn">edited</span>
                      )}
                      {isExcluded && (
                        <span className="badge badge-err">excluded</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={15} className="muted" style={{ textAlign: "center" }}>
                    {api14s.length === 0
                      ? "select wells on the map and forecast them before reviewing"
                      : "no rows match the current filters"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <ReviewMap
          forecasts={oilRows}
          excludedApi14s={excluded}
          formationFilter={formationFilter}
        />
      </div>

      <aside className="panel panel-right">
        <header className="panel-header">
          <span>Review summary</span>
        </header>
        <Stat label="In scope" value={oilRows.length.toString()} />
        <Stat label="After filters" value={filtered.length.toString()} />
        <Stat
          label="Included"
          value={`${includedCount} (${pct(includedCount, filtered.length)})`}
        />
        <Stat
          label="Excluded"
          value={`${excludedCount} (${pct(excludedCount, filtered.length)})`}
        />
        <Stat
          label="Outliers"
          value={`${outlierCount} (${pct(outlierCount, filtered.length)})`}
        />

        {outliers.stats && (
          <section className="filter-section">
            <h3>EUR / lateral-ft</h3>
            <Stat label="Median" value={outliers.stats.median.toFixed(1)} />
            <Stat label="Mean" value={outliers.stats.mean.toFixed(1)} />
            <Stat label="σ" value={outliers.stats.stddev.toFixed(1)} />
            <Stat
              label="2σ band"
              value={`${outliers.stats.lowThreshold.toFixed(0)} – ${outliers.stats.highThreshold.toFixed(0)}`}
            />
          </section>
        )}

        <section className="filter-section">
          <h3>Di (1-yr effective) — median</h3>
          <Stat label="Oil" value={fmtDi(diMedians.oil)} />
          <Stat label="Gas" value={fmtDi(diMedians.gas)} />
          <Stat label="Water" value={fmtDi(diMedians.water)} />
        </section>

        <button
          type="button"
          className="btn-primary"
          disabled={includedCount === 0}
          title="Open the Type curve page with the included set"
          onClick={() => setCurrentPage("type_curve")}
        >
          Aggregate {includedCount} into type curve →
        </button>
      </aside>

      {openApi14 && (() => {
        // Resolve nav position against the CURRENT sorted+filtered list
        // so prev/next walks the same order the user sees in the table.
        // If the row dropped out of view since opening (e.g. user toggled
        // "outliers only"), currentIndex is -1 and nav is disabled.
        const currentIndex = sorted.findIndex((r) => r.api14 === openApi14);
        const onPrev =
          currentIndex > 0
            ? () => setOpenApi14(sorted[currentIndex - 1]!.api14)
            : undefined;
        const onNext =
          currentIndex >= 0 && currentIndex < sorted.length - 1
            ? () => setOpenApi14(sorted[currentIndex + 1]!.api14)
            : undefined;
        const position =
          currentIndex >= 0
            ? { index: currentIndex + 1, total: sorted.length }
            : undefined;
        return (
          <ForecastDetailModal
            api14={openApi14}
            forecasts={allForecastsCorrected.filter((f) => f.api14 === openApi14)}
            onClose={() => setOpenApi14(null)}
            onSaved={(updated) =>
              setAllForecasts((prev) =>
                prev.map((f) => (f.id === updated.id ? updated : f)),
              )
            }
            onPrev={onPrev}
            onNext={onNext}
            position={position}
          />
        );
      })()}
    </div>
  );
}

function pct(n: number, d: number): string {
  if (d === 0) return "0%";
  return `${Math.round((n / d) * 100)}%`;
}
