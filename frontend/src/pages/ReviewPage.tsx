// Review page: sortable, filterable table of forecasted wells with
// outlier flagging on EUR-per-lateral-foot. Per-well checkbox to
// include/exclude from type-curve aggregation (step 6).
//
// Also drives the cohort autoforecast: on selection change the page
// fires the batch endpoint with the short-history cutoff stored in
// mapStore (set on the Map tab's right panel). Wells with sufficient
// history get fit; the rest land in `shortApi10s` and the user runs
// `transferCohortParams` from the right panel to write transferred
// forecasts using the long cohort's median Di / b.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type ForecastRow,
  type TransferError,
  TransferErrorThrown,
  batchForecast,
  fetchSyncJob,
  listForecasts,
  transferCohortParams,
  type Stream,
} from "../api/forecasts";
import { fetchWellDetails, type WellDetailLite } from "../api/wells";
import { ForecastDetailModal } from "../forecasts/ForecastDetailModal";
import { computeOutliers } from "../forecasts/outliers";
import { Stat, Th, type SortDir } from "../forecasts/reviewTable";
import {
  effDiFor,
  fmtDi,
  fmtInt,
  fmtVol,
  indexFitsByWellStream,
  median,
} from "../forecasts/reviewTableHelpers";
import { ExclusionReasonControl } from "../components/ExclusionReasonControl";
import { ReviewMap } from "../components/ReviewMap";
import { WaterSourceBadge } from "../components/WaterSourceBadge";
import type { ProvenanceDraft, SelectionEvent } from "../api/types";
import { manualExclusionsFromEvents } from "../store/cohortProvenance";
import { activeCohort as activeCohortSel, useCohortStore } from "../store/cohortStore";
import { useMapStore } from "../store/mapStore";

type SortKey =
  | "api10"
  | "well_name"
  | "formation"
  | "operator"
  | "first_prod_date"
  | "lateral"
  | "eur"
  | "novi_oil_eur"
  | "eur_per_ft"
  | "fit_r2"
  | "oil_di"
  | "gas_di"
  | "water_di"
  | "downtime";

type FitStatus = "idle" | "running" | "succeeded" | "failed";
const DEFAULT_MIN_DONOR_COUNT = 5;

export function ReviewPage() {
  const api10s = useMapStore((s) => s.forecastApi10s);
  const exclusions = useMapStore((s) => s.exclusions);
  const toggleExcluded = useMapStore((s) => s.toggleExcluded);
  const setExclusionReason = useMapStore((s) => s.setExclusionReason);
  const setCurrentPage = useMapStore((s) => s.setCurrentPage);
  // Derived Set view — downstream consumers (outlier scope, ReviewMap,
  // the Aggregate filter) only care about membership, not reasons.
  const excluded = useMemo(
    () => new Set(exclusions.keys()),
    [exclusions],
  );

  // Partition lives in the store so it survives nav back and forth
  // between Review and other tabs. The pending-transfer badges + the
  // banner read from this.
  const partition = useMapStore((s) => s.forecastPartition);
  const setPartition = useMapStore((s) => s.setForecastPartition);
  const triggerPending = useMapStore((s) => s.forecastTriggerPending);
  const setTriggerPending = useMapStore((s) => s.setForecastTriggerPending);
  // Fit status moved to the store so the polling loop can write
  // updates even if the component unmounts/remounts mid-poll (e.g.
  // StrictMode's double-effect-run, or user navigating Review → Map
  // → Review while the long fit is still running on the backend).
  const fitStatus = useMapStore((s) => s.fitStatus);
  const setFitStatus = useMapStore((s) => s.setFitStatus);
  const fitStatusNote = useMapStore((s) => s.fitStatusNote);
  const setFitStatusNote = useMapStore((s) => s.setFitStatusNote);

  const longApi10s = partition?.long ?? [];
  // Memoized: consumed in downstream hook deps (useCallback/useMemo),
  // so a fresh `?? []` array every render would churn them.
  const shortApi10s = useMemo(() => partition?.short ?? [], [partition]);
  const noPeakApi10s = partition?.noPeak ?? [];

  const [allForecasts, setAllForecasts] = useState<ForecastRow[]>([]);
  // Stub oil rows synthesized for short-history wells that have NO
  // oil forecast row yet. Populated by the batch+poll flow whenever
  // the partition's short list contains wells not present in
  // `allForecasts`. Each stub carries the well's headers plus null
  // forecast fields so the Review table can render them with the
  // pending-transfer badge instead of dropping them entirely.
  const [unfitStubs, setUnfitStubs] = useState<ForecastRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("eur_per_ft");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [formationFilter, setFormationFilter] = useState<string>("all");
  const [showOnlyOutliers, setShowOnlyOutliers] = useState(false);
  const [openApi10, setOpenApi10] = useState<string | null>(null);

  // ---- Cohort transfer state (transient, stays local) ----
  // See the Autoforecast section in the right panel — status pill,
  // pending-transfer summary, and the cohort-transfer button.
  const [transferStatus, setTransferStatus] = useState<FitStatus>("idle");
  const [transferNote, setTransferNote] = useState<string>("");
  const [transferError, setTransferError] = useState<TransferError | null>(null);
  // Cutoff used for the last batch — the transfer endpoint must
  // honor the same value the partition was computed against so the
  // donor pool doesn't silently shift if the user reopens the map
  // and changes the cutoff field after firing. Initialized from the
  // persisted partition so a refresh / nav-return restores it.
  const lastSubmittedCutoff = useRef<number>(
    partition?.cutoffUsed ?? useMapStore.getState().forecastCutoffMonths,
  );

  const refreshForecasts = useCallback(async () => {
    if (api10s.length === 0) return;
    const rows = await listForecasts(api10s);
    setAllForecasts(rows);
  }, [api10s]);

  // Effect C: synthesize stub oil rows for short-history wells that
  // don't have a forecast yet. Without this, those wells just don't
  // appear in the table — there's nothing for the pending-transfer
  // badge to attach to. After the user clicks Apply cohort params,
  // real cohort_transfer rows replace the stubs and they'll appear
  // via the normal listForecasts path; setUnfitStubs([]) clears them.
  useEffect(() => {
    if (shortApi10s.length === 0) {
      if (unfitStubs.length > 0) setUnfitStubs([]);
      return;
    }
    const realOilApis = new Set(
      allForecasts.filter((f) => f.stream === "oil").map((f) => f.api10),
    );
    const missing = shortApi10s.filter((a) => !realOilApis.has(a));
    if (missing.length === 0) {
      if (unfitStubs.length > 0) setUnfitStubs([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const details = await fetchWellDetails(missing);
        if (cancelled) return;
        setUnfitStubs(details.map(stubRowFromWellDetail));
      } catch {
        if (!cancelled) setUnfitStubs([]);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shortApi10s.join(","), allForecasts]);

  // Effect A: always populate the table when the selection changes.
  // This is a read-only fetch — never fires the batch endpoint, so
  // navigating to / from Review never triggers a fit.
  useEffect(() => {
    if (api10s.length === 0) return;
    let cancelled = false;
    void (async () => {
      setLoading(true);
      try {
        const rows = await listForecasts(api10s);
        if (!cancelled) setAllForecasts(rows);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api10s.join(",")]);

  // Effect B: fire the batch ONLY when the Map's Forecast button set
  // forecastTriggerPending. The flag is consumed immediately so the
  // effect doesn't re-fire. Critically: NO local `cancelled` flag —
  // the polling writes to the store (fitStatus / fitJobId), so a
  // StrictMode simulated unmount or a real tab nav doesn't kill the
  // in-flight poller. If two effect runs happen concurrently (which
  // StrictMode does in dev), the fitJobId guard in mapStore prevents
  // the second one from kicking off another batch.
  useEffect(() => {
    if (!triggerPending) return;
    if (api10s.length === 0) return;
    // SYNCHRONOUS in-flight guard. StrictMode runs the effect twice
    // in quick succession with a simulated unmount in between; the
    // first run flips fitStatus to "running" before the second run
    // even starts, so the second run bails here.
    if (useMapStore.getState().fitStatus === "running") return;
    setTriggerPending(false);
    setTransferStatus("idle");
    setTransferNote("");
    setTransferError(null);
    setPartition(null);

    const cutoff = useMapStore.getState().forecastCutoffMonths;
    lastSubmittedCutoff.current = cutoff;
    setFitStatus("running");  // synchronous claim — see guard above
    setFitStatusNote(
      `partitioning ${api10s.length} wells (cutoff ${cutoff} mo)…`,
    );

    void (async () => {
      try {
        const b = await batchForecast(api10s, {
          short_history_cutoff_months: cutoff,
        });
        useMapStore.getState().setFitJobId(b.job_id);
        const long = b.long_api10s ?? [];
        const short = b.short_api10s ?? [];
        const noPeak = b.no_peak_api10s ?? [];
        setPartition({ long, short, noPeak, cutoffUsed: cutoff });
        setFitStatusNote(
          `job ${b.job_id.slice(0, 8)} — fitting ${long.length} long-history wells…`,
        );
        // Direct per-id lookup avoids the recent_jobs-window race.
        for (let i = 0; i < 600; i++) {
          await new Promise((r) => setTimeout(r, 1000));
          const job = await fetchSyncJob(b.job_id);
          if (!job) continue;
          if (job.status === "succeeded") {
            setFitStatus("succeeded");
            setFitStatusNote(
              `fit complete — ${job.items_upserted}/${long.length} long; ` +
                `${short.length} short pending transfer` +
                (noPeak.length > 0 ? ` (${noPeak.length} no peak)` : ""),
            );
            break;
          }
          if (job.status === "failed") {
            setFitStatus("failed");
            setFitStatusNote(job.error ?? "fit failed");
            return;
          }
        }
        await refreshForecasts();
      } catch (e) {
        setFitStatus("failed");
        setFitStatusNote(String(e));
      } finally {
        // Clear the job-id guard so a NEXT Forecast click can start
        // a fresh batch.
        useMapStore.getState().setFitJobId(null);
      }
    })();
    // No cleanup — we deliberately want the polling to keep running
    // across remounts and tab navigation. The job-id guard above
    // prevents duplicate kickoffs; the store-backed status survives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerPending]);

  const onApplyTransfer = useCallback(async () => {
    if (shortApi10s.length === 0) return;
    setTransferStatus("running");
    setTransferNote(`transferring cohort params to ${shortApi10s.length} wells…`);
    setTransferError(null);
    try {
      const resp = await transferCohortParams({
        api10s,
        short_history_cutoff_months: lastSubmittedCutoff.current,
        min_donor_count: DEFAULT_MIN_DONOR_COUNT,
      });
      const oilDonor = resp.donors.find((d) => d.stream === "oil");
      const summary = oilDonor
        ? `Di=${oilDonor.cohort_di.toFixed(2)}, ` +
          `b=${oilDonor.cohort_b.toFixed(2)} from ${oilDonor.donor_count} donors`
        : "no oil donor summary";
      setTransferStatus("succeeded");
      setTransferNote(
        `transferred ${resp.written_api10s.length} wells — ${summary}` +
          (resp.skipped_locked.length > 0
            ? ` (${resp.skipped_locked.length} stream-rows locked)`
            : "") +
          (resp.skipped_no_peak.length > 0
            ? ` (${resp.skipped_no_peak.length} no peak)`
            : ""),
      );
      // Banner disappears — short wells now have forecasts. Keep the
      // long + no-peak buckets in the partition so the cutoffUsed
      // value persists, and so re-running the transfer is a no-op
      // until the user fires a new batch.
      if (partition) {
        setPartition({ ...partition, short: [] });
      }
      await refreshForecasts();
    } catch (e) {
      setTransferStatus("failed");
      if (e instanceof TransferErrorThrown) {
        setTransferError(e.payload);
        setTransferNote(
          `donor cohort too thin (${e.payload.donor_count} oil wells, ` +
            `need ≥${e.payload.min_required})`,
        );
      } else {
        setTransferNote(String(e));
      }
    }
  }, [api10s, shortApi10s, partition, setPartition, refreshForecasts]);

  // Use the backend-computed Method-1 EUR (actual cum to date +
  // model's projection from end-of-history to year 50) as the
  // canonical `eur` for every Review-tab surface: the table cell,
  // EUR/ft sort, outlier stats, map color ramp. Falls back to the
  // raw stored model EUR if the Method-1 field is missing (e.g. a
  // stub row synthesized for an un-fit well).
  const allForecastsCorrected = useMemo(
    () =>
      allForecasts.map((f) => ({
        ...f,
        eur: f.eur_displayed ?? f.eur,
      })),
    [allForecasts],
  );

  // Show one row per WELL (use the oil forecast — the brief frames type
  // curves as per-well, and gas/water inherit oil's peak). Merge in
  // any synthesized stub rows for short-history wells that don't yet
  // have an oil forecast, so they render with the pending-transfer
  // badge instead of disappearing from the table.
  const oilRows = useMemo(() => {
    const real = allForecastsCorrected.filter((f) => f.stream === "oil");
    if (unfitStubs.length === 0) return real;
    const realApis = new Set(real.map((f) => f.api10));
    return [...real, ...unfitStubs.filter((s) => !realApis.has(s.api10))];
  }, [allForecastsCorrected, unfitStubs]);

  // Per-(api10, stream) lookup so the Di columns can pull each well's
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
          .filter((f) => !excluded.has(f.api10))
          .map((f) => ({
            api10: f.api10,
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
      rows = rows.filter((r) => outliers.outlierApi10s.has(r.api10));
    }
    return rows;
  }, [oilRows, formationFilter, showOnlyOutliers, outliers]);

  const sorted = useMemo(() => {
    const sign = sortDir === "asc" ? 1 : -1;
    const get = (r: ForecastRow): number | string => {
      switch (sortKey) {
        case "api10": return r.api10;
        case "well_name": return r.well_name ?? "";
        case "formation": return r.well_formation ?? "";
        case "operator": return r.well_operator ?? "";
        // ISO dates sort lexicographically (YYYY-MM-DD), so string
        // compare is correct here without a Date parse.
        case "first_prod_date": return r.well_first_prod_date ?? "";
        case "lateral": return r.well_lateral_ft ?? 0;
        case "eur": return r.eur ?? 0;
        case "novi_oil_eur": return r.well_novi_oil_eur ?? 0;
        case "eur_per_ft":
          return r.eur != null && r.well_lateral_ft
            ? r.eur / r.well_lateral_ft
            : 0;
        case "fit_r2": return r.fit_r2 ?? 0;
        case "oil_di":
          return effDiFor(fitsByWellStream.get(r.api10)?.get("oil")) ?? -1;
        case "gas_di":
          return effDiFor(fitsByWellStream.get(r.api10)?.get("gas")) ?? -1;
        case "water_di":
          return effDiFor(fitsByWellStream.get(r.api10)?.get("water")) ?? -1;
        case "downtime": return r.downtime_ratio ?? 0;
      }
    };
    return [...filtered].sort((a, b) => {
      const av = get(a), bv = get(b);
      if (typeof av === "string") return sign * av.localeCompare(bv as string);
      return sign * ((av) - (bv as number));
    });
  }, [filtered, sortKey, sortDir, fitsByWellStream]);

  function clickHeader(key: SortKey) {
    if (key === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const includedCount = filtered.filter((r) => !excluded.has(r.api10)).length;
  const excludedCount = filtered.filter((r) => excluded.has(r.api10)).length;
  const outlierCount = filtered.filter((r) => outliers.outlierApi10s.has(r.api10)).length;

  // O(1) lookup for the per-row "pending transfer" badge + tint.
  // Every well in shortApi10s gets badged — including ones that are
  // ALREADY cohort_transfer from a previous session. Reason: clicking
  // Apply re-runs the transfer with the current long-cohort median,
  // which may differ from the previous one (the user may have edited
  // long fits this session). So those rows truly are "pending an
  // updated transfer," not "already done." Matches the user mental
  // model of "10 short wells, 10 pending."
  const shortApi10sSet = useMemo(() => new Set(shortApi10s), [shortApi10s]);

  // Cohort-level median 1-yr effective Di across the currently INCLUDED
  // wells (matches how the outlier stats / EUR-per-ft band are scoped).
  const diMedians = useMemo(() => {
    const included = oilRows.filter((r) => !excluded.has(r.api10));
    const collect = (stream: Stream): number[] =>
      included
        .map((r) => effDiFor(fitsByWellStream.get(r.api10)?.get(stream)))
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
                <Th k="api10" sortKey={sortKey} sortDir={sortDir} onClick={clickHeader}>
                  api10
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
                <Th
                  k="novi_oil_eur"
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onClick={clickHeader}
                >
                  novi_oil_eur
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
                const isOut = outliers.outlierApi10s.has(r.api10);
                const isExcluded = excluded.has(r.api10);
                const isPendingTransfer = shortApi10sSet.has(r.api10);
                const eurPerFt =
                  r.eur != null && r.well_lateral_ft
                    ? r.eur / r.well_lateral_ft
                    : null;
                // Tiered highlight on the oil EUR cell when our value
                // diverges from Novi's 50-yr oil EUR. Both are full
                // model EURs, so the comparison is apples-to-apples.
                const eurDivPct =
                  r.eur != null &&
                  r.well_novi_oil_eur != null &&
                  r.well_novi_oil_eur !== 0
                    ? Math.abs(r.eur - r.well_novi_oil_eur) /
                      Math.abs(r.well_novi_oil_eur)
                    : null;
                const eurDivClass =
                  eurDivPct == null
                    ? ""
                    : eurDivPct > 0.3
                      ? "eur-diverge-strong"
                      : eurDivPct > 0.15
                        ? "eur-diverge-mild"
                        : "";
                const eurDivTitle =
                  eurDivPct == null
                    ? undefined
                    : `App vs Novi oil EUR: ${(eurDivPct * 100).toFixed(0)}% gap`;
                return (
                  <tr
                    key={r.api10}
                    className={
                      [
                        "row-clickable",
                        isOut ? "row-outlier" : "",
                        isExcluded ? "row-excluded" : "",
                        isPendingTransfer ? "row-pending-transfer" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")
                    }
                    onClick={() => setOpenApi10(r.api10)}
                  >
                    {/* The include/exclude checkbox cell must NOT open
                        the modal — stop propagation on both the cell
                        and the input. */}
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={!isExcluded}
                        onChange={() => toggleExcluded(r.api10)}
                      />
                    </td>
                    <td>{r.api10}</td>
                    <td>{r.well_name ?? "—"}</td>
                    <td>{r.well_formation ?? "—"}</td>
                    <td>{r.well_operator ?? "—"}</td>
                    <td>{r.well_first_prod_date ?? "—"}</td>
                    <td>{fmtInt(r.well_lateral_ft)}</td>
                    <td className={eurDivClass} title={eurDivTitle}>
                      {fmtVol(r.eur)}
                    </td>
                    <td>{fmtVol(r.well_novi_oil_eur)}</td>
                    <td>{eurPerFt != null ? eurPerFt.toFixed(1) : "—"}</td>
                    <td>{r.fit_r2 != null ? r.fit_r2.toFixed(3) : "—"}</td>
                    <td>{fmtDi(effDiFor(fitsByWellStream.get(r.api10)?.get("oil")))}</td>
                    <td>{fmtDi(effDiFor(fitsByWellStream.get(r.api10)?.get("gas")))}</td>
                    <td>{fmtDi(effDiFor(fitsByWellStream.get(r.api10)?.get("water")))}</td>
                    <td>
                      {r.downtime_ratio != null
                        ? `${(r.downtime_ratio * 100).toFixed(0)}%`
                        : "—"}
                    </td>
                    <td>
                      {isPendingTransfer && (
                        <span
                          className="badge badge-info"
                          title={
                            `Short post-peak history (< ${lastSubmittedCutoff.current} mo). ` +
                            `The displayed fit is stale from a previous run — click ` +
                            `"Apply cohort params" in the right panel to overwrite ` +
                            `with the long-history cohort's median Di / b.`
                          }
                        >
                          pending transfer
                        </span>
                      )}
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
                      {/* Water-provenance caution — 'calculated' means
                          the vendor water series is a static WOR x oil
                          formula, so the water fit tracks a fabricated
                          stream; indeterminate/insufficient are muted.
                          'measured' stays quiet in the table (the
                          detail modal shows the positive badge). Flag
                          only — nothing is auto-excluded. */}
                      {r.well_water_source != null &&
                        r.well_water_source !== "measured" && (
                          <WaterSourceBadge
                            source={r.well_water_source}
                            worCv={r.well_wor_cv}
                          />
                        )}
                      {/* A real fit exists for oil (non-stub row) but a
                          sibling stream has no forecast: the fitter found
                          no production peak in the detection window and
                          skipped it (e.g. lease-allocated water credited
                          only years after first prod). Surfaced so "no
                          forecast" in the modal isn't a mystery. */}
                      {!r.id.startsWith("stub-") &&
                        (["gas", "water"] as const)
                          .filter((st) => !fitsByWellStream.get(r.api10)?.get(st))
                          .map((st) => (
                            <span
                              key={st}
                              className="badge badge-muted"
                              title={
                                `No ${st} forecast — the stream had no production peak ` +
                                `in the first 12 months, so the fit was skipped. The ` +
                                `well contributes nothing to the type curve's ${st} band; ` +
                                `oil and the other streams aggregate normally.`
                              }
                            >
                              no {st} fit
                            </span>
                          ))}
                      {isExcluded && (
                        <>
                          <span className="badge badge-err">excluded</span>
                          <ExclusionReasonControl
                            code={
                              exclusions.get(r.api10)?.code ?? "other"
                            }
                            note={exclusions.get(r.api10)?.note ?? ""}
                            onChange={(code, note) =>
                              setExclusionReason(r.api10, code, note)
                            }
                          />
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
              {sorted.length === 0 && (
                <tr>
                  <td colSpan={16} className="muted" style={{ textAlign: "center" }}>
                    {api10s.length === 0
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
          excludedApi10s={excluded}
          formationFilter={formationFilter}
        />
      </div>

      <aside className="panel panel-right">
        <header className="panel-header">
          <span>Review summary</span>
        </header>

        <section className="filter-section">
          <h3>Autoforecast</h3>
          <div className="forecast-status-row">
            <span className={`status-pill status-${fitStatus}`}>{fitStatus}</span>
            {fitStatusNote && <span className="muted">{fitStatusNote}</span>}
          </div>
          {fitStatus === "succeeded" && shortApi10s.length > 0 && (
            <>
              <div className="muted forecast-transfer-summary">
                <strong>{longApi10s.length}</strong> long-history wells fit.{" "}
                <strong>{shortApi10s.length}</strong> short-history pending transfer
                {noPeakApi10s.length > 0 && (
                  <span> ({noPeakApi10s.length} no peak)</span>
                )}
                . Review the long fits and edit any that need attention before
                applying.
              </div>
              <div className="forecast-status-row">
                <span className={`status-pill status-${transferStatus}`}>
                  {transferStatus}
                </span>
                {transferNote && <span className="muted">{transferNote}</span>}
              </div>
              <button
                type="button"
                className="btn-primary"
                onClick={() => void onApplyTransfer()}
                disabled={transferStatus === "running"}
                title="Write transferred forecasts for short-history wells using the long cohort's median Di / b"
              >
                Apply cohort params to {shortApi10s.length} short-history{" "}
                {shortApi10s.length === 1 ? "well" : "wells"}
              </button>
            </>
          )}
          {transferError && (
            <div className="forecast-transfer-error">
              <strong>Cohort too thin.</strong> Long-history pool has{" "}
              {transferError.donor_count} oil wells; need ≥{" "}
              {transferError.min_required}. Widen the selection or wait until
              more wells accrue {lastSubmittedCutoff.current} months of
              post-peak history.
              <button
                type="button"
                className="link-btn"
                onClick={() => setTransferError(null)}
                style={{ marginLeft: 6 }}
              >
                dismiss
              </button>
            </div>
          )}
        </section>

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
          onClick={() => {
            // Snapshot the EXACT well list this button's count promises:
            // the formation/outlier-FILTERED rows minus exclusions.
            // TypeCurvePage aggregates this snapshot — previously it
            // used the full forecastApi10s scope, so a filtered Review
            // ("Aggregate 45") silently built the TC from all 120.
            const st = useMapStore.getState();
            st.setTypeCurveApi10s(
              filtered
                .filter((r) => !excluded.has(r.api10))
                .map((r) => r.api10),
            );
            // Build-up funnel snapshot for the eventual save. Events
            // come from the forecast cohort's narrative (the cohort id
            // rode the Forecast handoff); the legacy direct-lasso flow
            // falls back to the last draw as a single event. ALL
            // exclusions ride along — the backend waterfall only
            // applies them to wells that reach that stage.
            const cohortState = useCohortStore.getState();
            const handoffCohort = st.activeCohortId
              ? (cohortState.cohorts.find((c) => c.id === st.activeCohortId) ??
                null)
              : activeCohortSel(cohortState);
            const events: SelectionEvent[] = handoffCohort
              ? handoffCohort.provenance.events
              : st.lastDraw
                ? [
                    {
                      kind: st.lastDraw.kind,
                      at: st.lastDraw.at,
                      api10s: st.lastDraw.api10s,
                      polygon: st.lastDraw.polygon,
                      filters: st.lastDraw.filters,
                    },
                  ]
                : [];
            // v2: distill coded manual removals from the narrative, and
            // carry the draw-time formation scope (latest polygon/bbox
            // event's filter formations) so the saved universe matches
            // what the Buildup drawer showed. Empty → null → the server
            // infers from final membership as before.
            const manualExclusions = manualExclusionsFromEvents(events);
            let drawFormations: string[] | null = null;
            for (let i = events.length - 1; i >= 0; i--) {
              const ev = events[i]!;
              if (
                (ev.kind === "polygon" || ev.kind === "bbox") &&
                "formations" in ev.filters &&
                ev.filters.formations.length > 0
              ) {
                drawFormations = ev.filters.formations;
                break;
              }
            }
            const draft: ProvenanceDraft = {
              selection_events: events,
              partition: partition
                ? {
                    cutoff_months: partition.cutoffUsed,
                    short: partition.short,
                    no_peak: partition.noPeak,
                  }
                : null,
              exclusions: Object.fromEntries(exclusions),
              filter_snapshot: st.filters,
              manual_exclusions: manualExclusions,
              formations: drawFormations,
            };
            st.setBuildupDraft(draft);
            // One-shot trigger — TypeCurvePage consumes this to fire
            // the compute. Without it, tab nav back to TC would
            // re-fire the compute on every visit.
            st.setTypeCurveTriggerPending(true);
            setCurrentPage("type_curve");
          }}
        >
          Aggregate {includedCount} into type curve →
        </button>
      </aside>

      {openApi10 && (() => {
        // Resolve nav position against the CURRENT sorted+filtered list
        // so prev/next walks the same order the user sees in the table.
        // If the row dropped out of view since opening (e.g. user toggled
        // "outliers only"), currentIndex is -1 and nav is disabled.
        const currentIndex = sorted.findIndex((r) => r.api10 === openApi10);
        const onPrev =
          currentIndex > 0
            ? () => setOpenApi10(sorted[currentIndex - 1]!.api10)
            : undefined;
        const onNext =
          currentIndex >= 0 && currentIndex < sorted.length - 1
            ? () => setOpenApi10(sorted[currentIndex + 1]!.api10)
            : undefined;
        const position =
          currentIndex >= 0
            ? { index: currentIndex + 1, total: sorted.length }
            : undefined;
        return (
          <ForecastDetailModal
            api10={openApi10}
            forecasts={allForecastsCorrected.filter((f) => f.api10 === openApi10)}
            onClose={() => setOpenApi10(null)}
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

// Synthesize an oil ForecastRow from a WellDetailLite for short-
// history wells that don't have a forecast yet. Every fit-related
// field is null/empty so the table renders "—" across the row;
// the api10 is what shortApi10sSet matches on to attach the
// pending-transfer badge + blue tint. The id prefix lets future
// callers detect a stub if needed.
function stubRowFromWellDetail(w: WellDetailLite): ForecastRow {
  return {
    id: `stub-${w.api10}`,
    api10: w.api10,
    stream: "oil",
    model_type: "modified_hyperbolic",
    params: {},
    qi: null,
    di_initial: null,
    di_effective: null,
    b: null,
    df_terminal: null,
    qo: null,
    peak_index_months: null,
    eur: null,
    peak_month_date: null,
    peak_rate: null,
    fit_method: "rate_cum",
    fit_r2: null,
    fit_rmse: null,
    fit_at_bound: false,
    bound_note: null,
    downtime_ratio: null,
    diagnostics: null,
    manual_override: false,
    locked: false,
    updated_at: new Date(0).toISOString(),
    well_name: w.name,
    well_operator: w.operator,
    // Standardized formation to match the /forecasts list + edit paths,
    // which surface formation_blueox as `well_formation`.
    well_formation: w.formation_blueox,
    well_lateral_ft: w.lateral_ft,
    well_vintage_year: w.vintage_year,
    well_first_prod_date: w.first_prod_date,
    well_county: w.county,
    well_novi_oil_eur: w.novi_oil_eur,
    well_water_source: w.water_source,
    well_wor_cv: null,
    actual_cum: null,
    eur_remaining: null,
    eur_displayed: null,
  };
}
