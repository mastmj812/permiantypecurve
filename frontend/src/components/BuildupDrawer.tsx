// BuildupDrawer — the live type-well build-up table, docked on the
// right of the Map tab (opened from the CohortBar's Buildup button).
//
// Shows EVERY well in the cohort's stamped AOI ∩ formation scope with
// its CURRENT disposition — included / vintage / lateral / spacing /
// status-etc / not-selected(+coded reason) — recomputed (debounced) as
// the engineer adjusts filters and adds/removes wells. A drawer, not a
// modal, because liveness while curating is the whole point: slide the
// lateral bound with the table visible and watch rows migrate stages.
//
// The waterfall itself is computed SERVER-side by the same engine the
// export sheet uses (POST /type-curves/buildup/preview) — no TS mirror
// of the stage semantics (spacing sentinel, NULL rules) to drift.
//
// Formation scope: the filter panel's formations at DRAW time (they
// ride every polygon/bbox event). Empty → fall back to the distinct
// formation_blueox of current members, labeled "inferred". Recorded
// scope only — deliberately NOT a cull stage (a bench unticked later
// lands in not_selected, as decided).

import { useEffect, useMemo, useRef, useState } from "react";

import { fetchBuildupPreview } from "../api/typeCurves";
import type {
  BuildupPreview,
  BuildupPreviewRow,
  ExclusionEntry,
  GeoJsonPolygon,
} from "../api/types";
import { SPACING_SENTINEL_FT } from "../api/types";
import { fetchWellDetails } from "../api/wells";
import {
  activeCohort,
  useCohortStore,
} from "../store/cohortStore";
import { manualExclusionsFromEvents } from "../store/cohortProvenance";
import { useMapStore } from "../store/mapStore";
import { ReasonDialog } from "./ReasonDialog";

const DEBOUNCE_MS = 400;

// Stage → compact badge label + tone. Anything unknown falls back to
// the raw stage key in the neutral tone (fixed stage order lives
// server-side; this map is presentation only).
const STAGE_BADGES: Record<string, { label: string; tone: string }> = {
  included: { label: "included", tone: "ok" },
  vintage: { label: "vintage", tone: "cull" },
  lateral: { label: "lateral", tone: "cull" },
  spacing: { label: "spacing", tone: "cull" },
  filters_other: { label: "filters", tone: "cull" },
  not_selected: { label: "not selected", tone: "manual" },
  no_peak: { label: "no peak", tone: "cull" },
  short_history: { label: "short hist", tone: "cull" },
  review_excluded: { label: "review excl", tone: "manual" },
  post_save_removed: { label: "removed", tone: "manual" },
  unaccounted: { label: "unaccounted", tone: "warn" },
};

type SortKey = "disposition" | "api10" | "name" | "operator" | "vintage" | "lateral" | "spacing";

export function BuildupDrawer({ onClose }: { onClose: () => void }) {
  const cohort = useCohortStore(activeCohort);
  const addStaged = useCohortStore((s) => s.addStaged);
  const removeApi10s = useCohortStore((s) => s.removeApi10s);
  const filters = useMapStore((s) => s.filters);

  const [preview, setPreview] = useState<BuildupPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("disposition");
  const [removeTarget, setRemoveTarget] = useState<BuildupPreviewRow | null>(null);

  // ---- inputs derived from the cohort narrative ---------------------
  const cohortEvents = cohort?.provenance.events;
  const events = useMemo(() => cohortEvents ?? [], [cohortEvents]);

  const aoiPolygons = useMemo<GeoJsonPolygon[]>(
    () =>
      events
        .filter((e) => (e.kind === "polygon" || e.kind === "bbox") && e.polygon)
        .map((e) => e.polygon as GeoJsonPolygon),
    [events],
  );

  // Draw-time formation scope: the LATEST polygon/bbox event's filter
  // formations (each draw snapshots its filters).
  const drawFormations = useMemo<string[]>(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i]!;
      if ((e.kind === "polygon" || e.kind === "bbox") && "formations" in e.filters) {
        const f = e.filters.formations;
        if (f.length > 0) return f;
      }
    }
    return [];
  }, [events]);

  // Fallback scope when the engineer drew with no formation filter:
  // distinct formation_blueox of current members ("inferred" label —
  // same rule the server applies at save time).
  const [inferredFormations, setInferredFormations] = useState<string[]>([]);
  const memberKey = cohort?.api10s.join(",") ?? "";
  useEffect(() => {
    let cancelled = false;
    if (drawFormations.length > 0 || !cohort || cohort.api10s.length === 0) {
      setInferredFormations([]);
      return () => {
        cancelled = true;
      };
    }
    fetchWellDetails(cohort.api10s)
      .then((rows) => {
        if (cancelled) return;
        const distinct = new Set<string>();
        for (const r of rows) if (r.formation_blueox) distinct.add(r.formation_blueox);
        setInferredFormations([...distinct].sort());
      })
      .catch(() => {
        if (!cancelled) setInferredFormations([]);
      });
    return () => {
      cancelled = true;
    };
    // memberKey stands in for the api10s array identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawFormations.length, memberKey]);

  const formations = drawFormations.length > 0 ? drawFormations : inferredFormations;
  const formationsInferred = drawFormations.length === 0 && formations.length > 0;

  const manualExclusions = useMemo(
    () => manualExclusionsFromEvents(events),
    [events],
  );

  // ---- debounced preview fetch --------------------------------------
  // One effect over every input; latest-wins guard so a slow response
  // can't clobber a fresher one.
  const fetchSeq = useRef(0);
  useEffect(() => {
    if (!cohort) return;
    if (aoiPolygons.length === 0 || formations.length === 0) {
      setPreview(null);
      return;
    }
    const seq = ++fetchSeq.current;
    setLoading(true);
    const t = window.setTimeout(() => {
      fetchBuildupPreview({
        aoi_polygons: aoiPolygons,
        formations,
        filter_spec: filters,
        cohort_api10s: cohort.api10s,
        manual_exclusions: manualExclusions,
      })
        .then((p) => {
          if (fetchSeq.current === seq) {
            setPreview(p);
            setError(null);
          }
        })
        .catch((e) => {
          if (fetchSeq.current === seq) setError(String(e));
        })
        .finally(() => {
          if (fetchSeq.current === seq) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(t);
    // memberKey covers cohort.api10s; cohort identity is implied by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aoiPolygons, formations, filters, memberKey, manualExclusions]);

  // ---- row actions --------------------------------------------------
  function addRow(api10: string) {
    if (!cohort) return;
    // Store mutation, not a spatial select — sidesteps the 500-cap
    // /select path entirely; logs a click_add (which also clears any
    // stale manual-exclusion record for the well).
    addStaged(cohort.id, [api10], null);
  }

  function confirmRemove(reason: ExclusionEntry) {
    if (!cohort || !removeTarget) return;
    removeApi10s(cohort.id, [removeTarget.api10], reason);
    setRemoveTarget(null);
  }

  // ---- sorting ------------------------------------------------------
  const stageOrder = useMemo(() => {
    const order = new Map<string, number>();
    // Survivors first, then server waterfall order for cull stages.
    order.set("included", 0);
    (preview?.waterfall ?? []).forEach((w, i) => order.set(w.stage, i + 1));
    return order;
  }, [preview]);

  const sortedRows = useMemo(() => {
    const rows = [...(preview?.rows ?? [])];
    const cmp: Record<SortKey, (a: BuildupPreviewRow, b: BuildupPreviewRow) => number> = {
      disposition: (a, b) =>
        (stageOrder.get(a.disposition) ?? 99) - (stageOrder.get(b.disposition) ?? 99) ||
        a.api10.localeCompare(b.api10),
      api10: (a, b) => a.api10.localeCompare(b.api10),
      name: (a, b) => (a.name ?? "").localeCompare(b.name ?? ""),
      operator: (a, b) => (a.operator ?? "").localeCompare(b.operator ?? ""),
      vintage: (a, b) =>
        (a.first_prod_date ?? "9999").localeCompare(b.first_prod_date ?? "9999"),
      lateral: (a, b) => (a.lateral_ft ?? -1) - (b.lateral_ft ?? -1),
      spacing: (a, b) =>
        (a.lateral_closer_xy_ft ?? Infinity) - (b.lateral_closer_xy_ft ?? Infinity),
    };
    rows.sort(cmp[sortKey]);
    return rows;
  }, [preview, sortKey, stageOrder]);

  const memberSet = useMemo(() => new Set(cohort?.api10s ?? []), [cohort?.api10s]);

  if (!cohort) return null;

  const th = (key: SortKey, label: string) => (
    <th
      onClick={() => setSortKey(key)}
      style={{ cursor: "pointer" }}
      title={`Sort by ${label}`}
      className={sortKey === key ? "buildup-sorted" : undefined}
    >
      {label}
    </th>
  );

  return (
    <div className="buildup-drawer">
      <div className="buildup-drawer-header">
        <div>
          <strong>Build-up — {cohort.name}</strong>
          {preview && !preview.no_aoi && (
            <span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>
              universe {preview.universe_count}
              {preview.universe_truncated ? " (truncated!)" : ""} ·{" "}
              {preview.aoi_polygon_count} AOI polygon
              {preview.aoi_polygon_count === 1 ? "" : "s"}
              {preview.aoi_total_area_sq_mi != null
                ? ` · ${preview.aoi_total_area_sq_mi} sq mi`
                : ""}
            </span>
          )}
        </div>
        <button
          type="button"
          className="inspect-modal-close"
          onClick={onClose}
          aria-label="Close build-up drawer"
        >
          ✕
        </button>
      </div>

      <div className="buildup-drawer-scope muted">
        {formations.length > 0 ? (
          <>
            scope: {formations.join(", ")}
            {formationsInferred && (
              <span
                title="No formation filter was set when the AOI was drawn — scope inferred from current members (same rule the save applies)."
                style={{ color: "#b45309" }}
              >
                {" "}
                (inferred from cohort)
              </span>
            )}
          </>
        ) : (
          "scope: no formations known yet"
        )}
      </div>

      {aoiPolygons.length === 0 ? (
        <div className="buildup-drawer-empty" style={{ color: "#b45309" }}>
          ⚠ No AOI stamped on this cohort — there is no starting universe
          to build from. Circle the cohort's neighborhood with the lasso
          (through-strikes select wells but make no meaningful area),
          then click <em>Add staged</em> — re-adding members is fine, it
          stamps the AOI.
        </div>
      ) : formations.length === 0 ? (
        <div className="buildup-drawer-empty muted">
          Formation scope unknown — check formations in the filter panel
          (used at next draw) or add wells so it can be inferred.
        </div>
      ) : (
        <>
          {preview && (
            <div className="buildup-waterfall">
              <span className="buildup-waterfall-chip buildup-tone-ok">
                universe {preview.universe_count}
              </span>
              {preview.waterfall
                .filter((w) => w.culled > 0)
                .map((w) => (
                  <span
                    key={w.stage}
                    className="buildup-waterfall-chip buildup-tone-cull"
                    title={w.description}
                  >
                    −{w.culled} {STAGE_BADGES[w.stage]?.label ?? w.stage}
                  </span>
                ))}
              <span className="buildup-waterfall-chip buildup-tone-ok">
                = {preview.included_count} in cohort
              </span>
              {loading && <span className="muted">updating…</span>}
            </div>
          )}
          {error && (
            <div className="alert alert-error" style={{ fontSize: 11 }}>
              {error}
            </div>
          )}
          {preview?.notes.map((n) => (
            <div key={n} className="muted buildup-note">
              {n}
            </div>
          ))}
          <div className="buildup-table-wrap">
            <table className="buildup-table">
              <thead>
                <tr>
                  {th("disposition", "disposition")}
                  {th("api10", "api10")}
                  {th("name", "well")}
                  {th("operator", "operator")}
                  {th("vintage", "first prod")}
                  {th("lateral", "lateral ft")}
                  {th("spacing", "spacing ft")}
                  <th />
                </tr>
              </thead>
              <tbody>
                {sortedRows.map((r) => {
                  const badge = STAGE_BADGES[r.disposition] ?? {
                    label: r.disposition,
                    tone: "warn",
                  };
                  return (
                    <tr key={r.api10}>
                      <td>
                        <span className={`buildup-badge buildup-tone-${badge.tone}`}>
                          {badge.label}
                        </span>
                        {r.reason_label && (
                          <span
                            className="muted"
                            style={{ marginLeft: 4, fontSize: 10 }}
                            title={r.note ?? undefined}
                          >
                            {r.reason_label}
                          </span>
                        )}
                      </td>
                      <td className="mono">{r.api10}</td>
                      <td title={r.formation ?? undefined}>{r.name ?? "—"}</td>
                      <td>{r.operator ?? "—"}</td>
                      <td>{r.first_prod_date ?? "—"}</td>
                      <td className="num">
                        {r.lateral_ft != null
                          ? Math.round(r.lateral_ft).toLocaleString()
                          : "—"}
                      </td>
                      <td className="num">
                        {/* Name both no-spacing classes. An em-dash here read
                            as "nothing to see", hiding NULL-spacing wells
                            that the filter treats exactly like no-nbr. */}
                        {r.lateral_closer_xy_ft == null
                          ? "no data"
                          : r.lateral_closer_xy_ft === SPACING_SENTINEL_FT
                            ? "no-nbr"
                            : Math.round(r.lateral_closer_xy_ft).toLocaleString()}
                      </td>
                      <td>
                        {memberSet.has(r.api10) ? (
                          <button
                            type="button"
                            className="inspect-modal-linkbtn"
                            title="Remove from cohort (with a coded reason)"
                            onClick={() => setRemoveTarget(r)}
                          >
                            − remove
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="inspect-modal-linkbtn"
                            title="Add to cohort (logs a click_add; clears any prior coded removal)"
                            onClick={() => addRow(r.api10)}
                          >
                            + add
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {removeTarget && (
        <ReasonDialog
          title={`Remove ${removeTarget.name ?? removeTarget.api10}`}
          detail="The reason lands on the build-up sheet's not-selected stage."
          confirmLabel="Remove well"
          onConfirm={confirmRemove}
          onCancel={() => setRemoveTarget(null)}
        />
      )}
    </div>
  );
}
