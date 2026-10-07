// Deal dossier preview + export. Mounted chrome-free at
// `#/deals/<id>/dossier` (like the type-curve slide page). Renders the
// deal's SAVED Blue Ox config: a zone summary (which sticks take which
// curve, the cohort behind it, TC vs Novi — the deck's first slide,
// built server-side), then one section per pinned narvi scenario
// (live plan-view map + gunbarrel) followed by one section per zone
// type curve (param table + rate/cum charts + cohort map — the same
// panels as the slide export). The Export button captures every panel
// to PNG client-side and POSTs them to /api/deals/{id}/dossier.pptx.
//
// This is a working discussion deck, not a final exhibit — maps are
// live; frame them before exporting.

import { useEffect, useMemo, useState } from "react";

import {
  type BlueOxConfig,
  type DealRow,
  type DossierManifest,
  type DossierCurveTable,
  type DossierFunnel,
  type DossierZone,
  type DossierZonesResponse,
  type NoviComparisonZone,
  exportDealDossierPptx,
  fetchDossierZones,
  fetchNoviComparison,
  getBlueOxConfig,
  getDeal,
} from "../api/deals";
import { type Stream, type WellCurvesResponse, fetchWellCurves } from "../api/forecasts";
import {
  type NarviScenarioDetail,
  type NarviWellGeo,
  fetchNarviScenarioDetail,
} from "../api/narvi";
import { UNASSIGNED_COLOR, resolveZone, zoneColor } from "../map/blueoxZones";
import {
  type TypeCurveRow,
  type TypeCurveWellStat,
  fetchTypeCurve,
  fetchTypeCurveWellStats,
} from "../api/typeCurves";
import { type WellDetailLite, fetchWellDetails } from "../api/wells";
import { NO_SOURCE_FILE, fetchDealPolygons } from "../api/dealPolygons";
import { NoviComparisonPanel } from "../components/dossier/NoviComparisonPanel";
import { ShapefileSelect } from "../components/dossier/ShapefileSelect";
import { ScenarioGunBarrel } from "../components/dossier/ScenarioGunBarrel";
import { ScenarioSlideMap } from "../components/dossier/ScenarioSlideMap";
import { ZoneSupportMap } from "../components/dossier/ZoneSupportMap";
import { NO_FIT_COLOR } from "../components/dossier/mapLegend";
import { capturePanel } from "../components/slide/captureSlideComposite";
import { SlideCumChart } from "../components/slide/SlideCumChart";
import { SlideMap } from "../components/slide/SlideMap";
import { SlideParamTable } from "../components/slide/SlideParamTable";
import { SlideRateChart } from "../components/slide/SlideRateChart";

// Matches the backend's scenario picture boxes (5.95" x 4.9" at
// 96 px/in) so the captured PNG aspect equals the slide placement.
const SCENARIO_PANEL_W = 571;
const SCENARIO_PANEL_H = 470;
const STREAMS: Stream[] = ["oil", "gas", "water"];

interface Props {
  dealId: string;
}

// Well counts per handoff class + bench — the scenario slide subtitle.
function scenarioSubtitle(sd: NarviScenarioDetail): string {
  const parts: string[] = [];
  for (const cat of ["PUD", "UPSIDE"] as const) {
    const byBench = new Map<string, number>();
    for (const w of sd.wells) {
      if (w.category !== cat) continue;
      const key = w.formation ?? "(no bench)";
      byBench.set(key, (byBench.get(key) ?? 0) + 1);
    }
    if (byBench.size > 0) {
      parts.push(
        `${cat} ${[...byBench.entries()].map(([f, n]) => `${f} x${n}`).join(", ")}`,
      );
    }
  }
  const pdp = sd.wells.filter((w) => w.category === "PDP").length;
  if (pdp > 0) parts.push(`PDP x${pdp}`);
  parts.push(sd.well_type);
  return parts.join(" · ");
}

export function DealDossierPage({ dealId }: Props) {
  const [deal, setDeal] = useState<DealRow | null>(null);
  const [cfg, setCfg] = useState<BlueOxConfig | null>(null);
  const [scenarios, setScenarios] = useState<NarviScenarioDetail[] | null>(null);
  const [comparisons, setComparisons] = useState<NoviComparisonZone[] | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [zoneSummary, setZoneSummary] = useState<DossierZonesResponse | null>(null);
  const [zoneSummaryError, setZoneSummaryError] = useState<string | null>(null);
  const [curvesReady, setCurvesReady] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  // Acreage picker: distinct uploaded shapefiles + per-shapefile
  // visibility (absent/true = visible; default all visible). This page
  // is its own chrome-free route, so the Map tab's store doesn't carry
  // over — selection lives here and flows into every SlideMap, which
  // applies changes live via setFilter (framing survives).
  const [shapefiles, setShapefiles] = useState<string[]>([]);
  const [dealVisibility, setDealVisibility] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const prevMargin = document.body.style.margin;
    const prevBg = document.body.style.background;
    document.body.style.margin = "0";
    document.body.style.background = "#ffffff";
    return () => {
      document.body.style.margin = prevMargin;
      document.body.style.background = prevBg;
    };
  }, []);

  // Populate the acreage picker. Degrades to no control on failure —
  // the maps then show all shapefiles, the pre-picker behavior.
  useEffect(() => {
    let cancelled = false;
    void fetchDealPolygons()
      .then((rows) => {
        if (cancelled) return;
        const seen: string[] = [];
        for (const p of rows) {
          const key = p.source_file ?? NO_SOURCE_FILE;
          if (!seen.includes(key)) seen.push(key);
        }
        setShapefiles(seen);
      })
      .catch((e) => console.warn("acreage shapefile list failed", e));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [d, cfgResp] = await Promise.all([
          getDeal(dealId),
          getBlueOxConfig(dealId),
        ]);
        if (cancelled) return;
        setDeal(d);
        if (!cfgResp.config) {
          setError(
            "this deal has no saved Blue Ox config — save one first (the dossier renders its scenarios and zones)",
          );
          return;
        }
        setCfg(cfgResp.config);
        const details = await Promise.all(
          cfgResp.config.narvi_selections.map((s) =>
            fetchNarviScenarioDetail(s.deal_id, s.scenario_id),
          ),
        );
        if (!cancelled) setScenarios(details);
        // Zone summary — the export rebuilds it server-side, so a
        // failure here only blanks the preview table; say why.
        fetchDossierZones(dealId)
          .then((zs) => {
            if (!cancelled) setZoneSummary(zs);
          })
          .catch((e: unknown) => {
            if (!cancelled) setZoneSummaryError(e instanceof Error ? e.message : String(e));
          });
        // TC-vs-Novi benchmark — optional: an older backend or a
        // warehouse hiccup degrades to "no comparison sections", the
        // rest of the dossier still previews and exports. But say WHY:
        // silence here made a config error indistinguishable from
        // "no comparable analogs" (the diamonds lesson, 2026-07-30).
        try {
          const comps = await fetchNoviComparison(dealId);
          if (!cancelled) setComparisons(comps);
        } catch (e) {
          if (!cancelled) {
            setComparisons([]);
            setComparisonError(e instanceof Error ? e.message : String(e));
          }
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dealId]);

  // One curve section per zone, deduped (two zones may share a curve).
  const curveIds = useMemo(() => {
    if (!cfg) return [] as string[];
    return [...new Set(cfg.zones.map((z) => z.type_curve_id))];
  }, [cfg]);

  // Curve-assignment overview: every scenario's AOI + wells on ONE map,
  // each planned well colored by the zone (=> type curve) that captures
  // it under the SAVED config — the direct answer to "which curve is
  // each well getting" on a deal whose DSUs sit 20+ miles apart. PDP
  // producers are context, not assigned, and paint gray like uncovered
  // benches. Colors key on object identity (well names can repeat only
  // for PDP api10s shared across scenarios — same color either way).
  const overview = useMemo(() => {
    if (!scenarios || !cfg || cfg.zones.length === 0) return null;
    const wells: NarviWellGeo[] = [];
    const colors = new Map<NarviWellGeo, string>();
    const offsets = new Map<NarviWellGeo, number>();
    const geoms: unknown[] = [];
    // Stacked benches often share IDENTICAL plan-view laterals (Novi
    // puts e.g. BS2_S under BS3_C on one stick), so without a fan-out
    // the later-drawn zone paints over the other and its color
    // disappears from the overview (toucan: 4 red BS2_S sticks hidden
    // under 4 blue BS3_C). Offset each zone's legs a few screen px
    // perpendicular so every assignment stays visible.
    const zoneOffsetPx = (index: number) =>
      (index - (cfg.zones.length - 1) / 2) * 3;
    for (const sd of scenarios) {
      if (sd.aoi_geojson) {
        try {
          geoms.push(JSON.parse(sd.aoi_geojson));
        } catch {
          // skip an unparseable AOI; wells still render
        }
      }
      const ref = { deal_id: sd.deal_id, scenario_id: sd.scenario_id };
      for (const w of sd.wells) {
        wells.push(w);
        const rz =
          w.category === "PDP" ? null : resolveZone(w.formation, ref, cfg.zones);
        colors.set(w, rz ? zoneColor(rz.index) : UNASSIGNED_COLOR);
        offsets.set(w, rz ? zoneOffsetPx(rz.index) : 0);
      }
    }
    return {
      wells,
      colors,
      offsets,
      aoi: geoms.length
        ? JSON.stringify({ type: "GeometryCollection", geometries: geoms })
        : null,
    };
  }, [scenarios, cfg]);

  // Curve support slides: one per zone with planned sticks; index
  // order defines the z{i}_map / z{i}_zoom capture names. Colour = the
  // zone's index in the saved config (same palette as the overview).
  const supportZones = useMemo(() => {
    if (!zoneSummary || !cfg) return [] as Array<{ zone: DossierZone; color: string; aois: string[] }>;
    const aoiByRef = new Map<string, string>();
    for (const sd of scenarios ?? []) {
      if (sd.aoi_geojson) aoiByRef.set(`${sd.deal_id}/${sd.scenario_id}`, sd.aoi_geojson);
    }
    return zoneSummary.zones
      .map((zone, i) => ({
        zone,
        color: zoneColor(i),
        aois: [...new Set(zone.sticks.map((s) => s.scenario_ref))]
          .map((r) => aoiByRef.get(r))
          .filter((a): a is string => !!a),
      }))
      .filter((z) => z.zone.n_sticks > 0);
  }, [zoneSummary, cfg, scenarios]);

  const scenarioNames = useMemo(() => {
    const out: Record<string, string> = {};
    for (const sd of scenarios ?? []) out[`${sd.deal_id}/${sd.scenario_id}`] = sd.name ?? sd.scenario_id;
    return out;
  }, [scenarios]);

  // ONE colour scale for every support map in the deck (p5-p95 of all
  // curve wells' anduin oil EUR/ft) so zones read against each other.
  const eurRange = useMemo(() => {
    const vals = supportZones
      .flatMap((z) => z.zone.cohort.map((w) => w.oil_eur_per_ft))
      .filter((v): v is number => v !== null)
      .sort((a, b) => a - b);
    if (vals.length === 0) return { lo: 0, hi: 1 };
    const q = (f: number) => vals[Math.round(f * (vals.length - 1))]!;
    const lo = Math.floor(q(0.05));
    const hi = Math.ceil(q(0.95));
    return hi - lo < 2 ? { lo: lo - 1, hi: hi + 1 } : { lo, hi };
  }, [supportZones]);

  // Comparison sections render only zones with sticks; index order
  // here defines the n{i}_figure capture names and the manifest order.
  const comparisonZones = useMemo(
    () => (comparisons ?? []).filter((z) => z.n_sticks > 0),
    [comparisons],
  );

  const allReady =
    scenarios !== null &&
    comparisons !== null &&
    (zoneSummary !== null || zoneSummaryError !== null) &&
    curveIds.every((_, i) => curvesReady.has(i));

  const onExport = async () => {
    if (!scenarios) return;
    setExporting(true);
    setError(null);
    setNotice(null);
    try {
      const manifest: DossierManifest = {
        scenarios: scenarios.map((sd) => ({
          title: sd.name ?? sd.scenario_id,
          subtitle: scenarioSubtitle(sd),
        })),
        curves: curveIds.map((id) => ({ type_curve_id: id })),
        overview: overview
          ? {
              title: "Curve assignment overview",
              subtitle:
                "every selected scenario — planned wells coloured by the type curve their zone assigns; gray = PDP context or uncaptured",
            }
          : null,
        supports: supportZones.map(({ zone }) => supportTitles(zone)),
        comparisons: comparisonZones.map((z) => ({
          title: `${z.zone_name} — Type Curve vs Novi ML (n=${z.n_sticks}: ${z.n_pud} PUD / ${z.n_res} RES)`,
          subtitle: comparisonSubtitle(z),
        })),
      };
      const files: Record<string, Blob> = {};
      // Map panels mount lazily (live only near the viewport), so a
      // section the user never scrolled to has no snapshot img yet —
      // scroll it in, let the map mount, and wait for its first idle
      // snapshot before capturing. Panels the user already framed keep
      // their latest snapshot even after the live map tore down.
      const ensureMapSnapshot = async (panel: HTMLElement) => {
        if (!panel.querySelector(".slide-map")) return; // not a map panel
        if (panel.querySelector("img.slide-map-img")) return;
        panel.scrollIntoView({ block: "center" });
        const deadline = Date.now() + 30_000;
        while (Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, 250));
          if (panel.querySelector("img.slide-map-img")) return;
        }
        throw new Error(
          "map snapshot timed out — scroll through the dossier once and retry",
        );
      };
      const grab = async (name: string) => {
        const panel = document.querySelector<HTMLElement>(
          `[data-dossier-panel="${name}"]`,
        );
        if (!panel) throw new Error(`panel ${name} not rendered yet`);
        await ensureMapSnapshot(panel);
        files[name] = await capturePanel(document, panel, 2);
      };
      if (overview) await grab("overview_map");
      for (let i = 0; i < supportZones.length; i++) {
        await grab(`z${i}_map`);
        await grab(`z${i}_zoom`);
      }
      for (let i = 0; i < scenarios.length; i++) {
        await grab(`s${i}_map`);
        await grab(`s${i}_gunbarrel`);
      }
      for (let i = 0; i < curveIds.length; i++) {
        for (const stream of STREAMS) {
          await grab(`c${i}_rate_${stream}`);
          await grab(`c${i}_cum_${stream}`);
        }
        await grab(`c${i}_map`);
      }
      for (let i = 0; i < comparisonZones.length; i++) {
        await grab(`n${i}_figure`);
      }
      const filename = await exportDealDossierPptx(dealId, manifest, files);
      setNotice(`exported ${filename}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  if (error && !cfg) {
    return (
      <div className="slide-page slide-error">
        <h2>Couldn't build dossier</h2>
        <p>{error}</p>
      </div>
    );
  }
  if (!deal || !cfg) {
    return (
      <div className="slide-page slide-loading">
        <p>Building dossier preview…</p>
      </div>
    );
  }

  return (
    <div className="slide-page" style={{ padding: "12px 24px 48px" }}>
      <div
        className="slide-preview-controls"
        style={{ position: "sticky", top: 0, background: "#ffffff", zIndex: 10, padding: "8px 0" }}
      >
        <strong style={{ marginRight: 12 }}>Dossier — {deal.name}</strong>
        <button
          type="button"
          className="tb-btn"
          disabled={exporting || !allReady}
          title={
            allReady
              ? "capture every panel as framed and download the .pptx"
              : "panels still loading…"
          }
          onClick={() => void onExport()}
        >
          {exporting ? "exporting…" : "Export dossier .pptx"}
        </button>
        {shapefiles.length > 0 && (
          <span style={{ marginLeft: 12 }}>
            <ShapefileSelect
              options={shapefiles}
              visibility={dealVisibility}
              onChange={(sourceFile, visible) =>
                setDealVisibility((prev) => ({ ...prev, [sourceFile]: visible }))
              }
            />
          </span>
        )}
        <span className="muted" style={{ marginLeft: 12 }}>
          maps are live — drag / scroll-zoom to frame each shot; the export
          captures the current views
        </span>
        {error && <span style={{ color: "#dc2626", fontSize: 14, marginLeft: 12 }}>{error}</span>}
        {notice && !error && (
          <span style={{ color: "#15803d", fontSize: 14, marginLeft: 12 }}>{notice}</span>
        )}
      </div>

      {scenarios === null && <p className="muted">loading narvi scenarios…</p>}

      <ZoneSummarySection summary={zoneSummary} error={zoneSummaryError} />
      {zoneSummary && zoneSummary.lateral_rows.length > 0 && <LateralSection summary={zoneSummary} />}

      {supportZones.map(({ zone, color, aois }, i) => {
        const t = supportTitles(zone);
        return (
          <section key={zone.zone_name} style={{ marginTop: 20 }}>
            <h1 className="slide-title" style={{ borderLeft: `8px solid ${color}`, paddingLeft: 8 }}>
              {t.title}
            </h1>
            <p className="muted" style={{ margin: "2px 0 6px", fontSize: 15 }}>{t.subtitle}</p>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <div className="slide-panel" data-dossier-panel={`z${i}_map`}>
                <ZoneSupportMap
                  zone={zone} color={color} aois={aois} scenarioNames={scenarioNames} mode="support" eurRange={eurRange}
                  width={SCENARIO_PANEL_W} height={SCENARIO_PANEL_H} lazy
                />
              </div>
              <div className="slide-panel" data-dossier-panel={`z${i}_zoom`}>
                <ZoneSupportMap
                  zone={zone} color={color} aois={aois} scenarioNames={scenarioNames} mode="sticks" eurRange={eurRange}
                  width={SCENARIO_PANEL_W} height={SCENARIO_PANEL_H} lazy
                />
              </div>
            </div>
            <SupportLegend color={color} range={eurRange} />
          </section>
        );
      })}

      {overview && (
        <section style={{ marginTop: 16 }}>
          <h1 className="slide-title">Curve assignment overview</h1>
          <p className="muted" style={{ margin: "2px 0 6px", fontSize: 15 }}>
            every selected scenario on one map — planned wells colored by
            the type curve their zone assigns under the saved config; gray
            = PDP context or a well no zone captures
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", margin: "0 0 6px", fontSize: 14 }}>
            {cfg.zones.map((z, i) => (
              <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                <span style={{
                  width: 12, height: 12, borderRadius: 2, display: "inline-block",
                  background: zoneColor(i),
                }} />
                {z.zone_name ?? z.type_curve_id.slice(0, 8)}
                {!!z.scenario_scope?.length && (
                  <span className="muted">({z.scenario_scope.length} DSU{z.scenario_scope.length === 1 ? "" : "s"})</span>
                )}
              </span>
            ))}
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <span style={{
                width: 12, height: 12, borderRadius: 2, display: "inline-block",
                background: UNASSIGNED_COLOR,
              }} />
              <span className="muted">PDP / unassigned</span>
            </span>
          </div>
          <div className="slide-panel" data-dossier-panel="overview_map">
            <ScenarioSlideMap
              aoiGeojson={overview.aoi}
              wells={overview.wells}
              width={1160}
              height={520}
              lazy
              colorForWell={(w) => overview.colors.get(w) ?? UNASSIGNED_COLOR}
              offsetForWell={(w) => overview.offsets.get(w) ?? 0}
              legend={{
                rows: [
                  ...cfg.zones.map((z, i) => ({
                    color: zoneColor(i),
                    label: z.zone_name ?? z.type_curve_id.slice(0, 8),
                    kind: "line" as const,
                  })),
                  { color: UNASSIGNED_COLOR, label: "PDP / unassigned", kind: "line" as const },
                ],
              }}
            />
          </div>
        </section>
      )}

      {scenarios?.map((sd, i) => (
        <section key={`${sd.deal_id}/${sd.scenario_id}`} style={{ marginTop: 20 }}>
          <h1 className="slide-title">{sd.name ?? sd.scenario_id}</h1>
          <p className="muted" style={{ margin: "2px 0 8px", fontSize: 15 }}>
            {scenarioSubtitle(sd)}
          </p>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <div className="slide-panel" data-dossier-panel={`s${i}_map`}>
              <ScenarioSlideMap
                aoiGeojson={sd.aoi_geojson}
                wells={sd.wells}
                width={SCENARIO_PANEL_W}
                height={SCENARIO_PANEL_H}
                lazy
              />
            </div>
            <div className="slide-panel" data-dossier-panel={`s${i}_gunbarrel`}>
              <ScenarioGunBarrel
                wells={sd.wells}
                azimuthDeg={sd.azimuth_deg}
                width={SCENARIO_PANEL_W}
                height={SCENARIO_PANEL_H}
              />
            </div>
          </div>
        </section>
      ))}

      {curveIds.map((id, i) => (
        <DossierCurveSection
          key={id}
          curveId={id}
          idx={i}
          table={zoneSummary?.curve_tables.find((t) => t.type_curve_id === id) ?? null}
          tableHeaders={zoneSummary?.cohort_headers ?? []}
          tableNote={zoneSummary?.cohort_note ?? ""}
          dealVisibility={dealVisibility}
          onReady={() =>
            setCurvesReady((prev) => new Set(prev).add(i))
          }
        />
      ))}

      {comparisonError && (
        <section style={{ marginTop: 20 }}>
          <h1 className="slide-title">Type Curve vs Novi ML</h1>
          <p style={{ color: "#dc2626", fontSize: 15 }}>
            comparison unavailable — {comparisonError}
          </p>
        </section>
      )}

      {comparisonZones.map((z, i) => (
        <NoviComparisonSection key={z.zone_name} zone={z} idx={i} />
      ))}

      {/* n=0 zones get an explicit placeholder (preview-only, no slide,
          no capture panel) — an empty analog neighborhood must read as a
          data condition, not a missing feature. */}
      {(comparisons ?? [])
        .filter((z) => z.n_sticks === 0)
        .map((z) => (
          <section key={z.zone_name} style={{ marginTop: 20 }}>
            <h1 className="slide-title">
              {z.zone_name} — Type Curve vs Novi ML
            </h1>
            <p className="muted" style={{ fontSize: 15, maxWidth: 900 }}>
              no comparison: none of the zone&apos;s{" "}
              {z.n_wells_no_set} planned well
              {z.n_wells_no_set === 1 ? "" : "s"} has a representative
              Novi stick set — no same-bench PUD/RES stick within{" "}
              {(z.radius_m / 1609).toFixed(0)} mi and ±
              {Math.round(z.lateral_tol * 100)}% lateral length
              {z.intel_vintage ? ` (intel vintage ${z.intel_vintage})` : ""}.
              This zone ships in the workbook as declared-empty; it gets no
              dossier slide.
            </p>
          </section>
        ))}
    </div>
  );
}

// Alignment/normalization/risking basis line under the comparison
// title — every convention that affects how the overlay reads.
function comparisonSubtitle(z: NoviComparisonZone): string {
  const parts = [
    "per 1,000 ft lateral",
    `TC aligned to peak${z.tc_risked ? " (risked)" : ""}, Novi to IP`,
    `median of ${z.n_sticks} representative novi_intel sticks (${z.n_self} self / ${z.n_neighborhood} neighborhood)`,
    // selection band — per-basin ll tolerance since 2026-07-30
    `selected within ${(z.radius_m / 1609).toFixed(0)} mi, ±${Math.round(z.lateral_tol * 100)}% ll`,
  ];
  if (z.intel_vintage) parts.push(`intel vintage ${z.intel_vintage}`);
  if (z.prev_intel_vintage && z.prev_n_sticks > 0) {
    parts.push(
      `prior-vintage overlay ${z.prev_intel_vintage} (drop-time set, ${z.prev_n_sticks} sticks)`,
    );
  }
  if (z.low_n) parts.push("LOW N");
  if (z.stale_vintage) parts.push("STALE VINTAGE");
  if (z.n_wells_no_set > 0) {
    parts.push(`${z.n_wells_no_set} wells without a representative set`);
  }
  return parts.join(" · ");
}

interface CurveSectionProps {
  curveId: string;
  idx: number;
  // Header acreage picker's selection — live prop on the SlideMap.
  dealVisibility: Record<string, boolean>;
  onReady: () => void;
  // Server-built well table (the deck carries the same rows); null while
  // the zone summary loads or when it failed.
  table: DossierCurveTable | null;
  tableHeaders: string[];
  tableNote: string;
}

interface CurveData {
  curve: TypeCurveRow;
  wellStats: TypeCurveWellStat[];
  wellDetails: WellDetailLite[];
  wellCurves: WellCurvesResponse[];
}

// One type curve's dossier section — the same data + panels as the
// slide export page (TypeCurveSlidePage), all streams stacked visibly.
function DossierCurveSection({
  curveId,
  idx,
  dealVisibility,
  onReady,
  table,
  tableHeaders,
  tableNote,
}: CurveSectionProps) {
  const [data, setData] = useState<CurveData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [curve, wellStats] = await Promise.all([
          fetchTypeCurve(curveId),
          fetchTypeCurveWellStats(curveId),
        ]);
        if (cancelled) return;
        const api10s = curve.included_api10s ?? [];
        const [wellDetails, wellCurvesSettled] = await Promise.all([
          api10s.length > 0 ? fetchWellDetails(api10s) : Promise.resolve([]),
          Promise.allSettled(api10s.map((a) => fetchWellCurves(a))),
        ]);
        if (cancelled) return;
        const wellCurves: WellCurvesResponse[] = [];
        for (const r of wellCurvesSettled) {
          if (r.status === "fulfilled") wellCurves.push(r.value);
        }
        setData({ curve, wellStats, wellDetails, wellCurves });
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [curveId]);

  useEffect(() => {
    if (data) onReady();
    // onReady is a stable-enough parent callback; firing once per data
    // load is the contract.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const lateralByApi10 = useMemo(() => {
    const m = new Map<string, number | null>();
    if (!data) return m;
    for (const s of data.wellStats) m.set(s.api10, s.lateral_ft);
    for (const d of data.wellDetails) {
      if (!m.has(d.api10) || m.get(d.api10) == null) m.set(d.api10, d.lateral_ft);
    }
    return m;
  }, [data]);

  if (error) {
    return (
      <section style={{ marginTop: 24 }}>
        <p style={{ color: "#dc2626" }}>curve {curveId}: {error}</p>
      </section>
    );
  }
  if (!data) {
    return (
      <section style={{ marginTop: 24 }}>
        <p className="muted">loading type curve…</p>
      </section>
    );
  }

  const api10s = data.curve.included_api10s ?? [];
  return (
    <section style={{ marginTop: 28 }}>
      <h1 className="slide-title">{data.curve.name}</h1>
      {table && <CohortTable table={table} headers={tableHeaders} note={tableNote} />}
      {table?.funnel && <FunnelTables funnel={table.funnel} />}
      <SlideParamTable current={data.curve} previous={null} />
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
        <div className="slide-panel slide-panel-map" data-dossier-panel={`c${idx}_map`}>
          <SlideMap
            api10s={api10s}
            wellDetails={data.wellDetails}
            dealVisibility={dealVisibility}
            width={629}
            height={418}
            lazy
          />
        </div>
      </div>
      {STREAMS.map((stream) => (
        <div
          key={stream}
          style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}
        >
          <div className="slide-panel" data-dossier-panel={`c${idx}_rate_${stream}`}>
            <SlideRateChart
              current={data.curve}
              previous={null}
              wellCurves={data.wellCurves}
              lateralByApi10={lateralByApi10}
              stream={stream}
            />
          </div>
          <div className="slide-panel" data-dossier-panel={`c${idx}_cum_${stream}`}>
            <SlideCumChart
              current={data.curve}
              previous={null}
              wellCurves={data.wellCurves}
              lateralByApi10={lateralByApi10}
              stream={stream}
            />
          </div>
        </div>
      ))}
    </section>
  );
}

interface NoviComparisonSectionProps {
  zone: NoviComparisonZone;
  idx: number;
}

// One zone's TC-vs-Novi comparison figure (6 panels), captured as a
// single full-width PNG for its dossier slide. Fetches the zone's
// curve itself — the curve sections dedupe by curve id while these
// key by zone, so sharing state would tangle the two lists.
function NoviComparisonSection({ zone, idx }: NoviComparisonSectionProps) {
  const [curve, setCurve] = useState<TypeCurveRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const c = await fetchTypeCurve(zone.type_curve_id);
        if (!cancelled) setCurve(c);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [zone.type_curve_id]);

  if (error) {
    return (
      <section style={{ marginTop: 24 }}>
        <p style={{ color: "#dc2626" }}>
          comparison {zone.zone_name}: {error}
        </p>
      </section>
    );
  }
  if (!curve) {
    return (
      <section style={{ marginTop: 24 }}>
        <p className="muted">loading TC-vs-Novi comparison…</p>
      </section>
    );
  }

  return (
    <section style={{ marginTop: 28 }}>
      <h1 className="slide-title">
        {zone.zone_name} — Type Curve vs Novi ML (n={zone.n_sticks}:{" "}
        {zone.n_pud} PUD / {zone.n_res} RES)
      </h1>
      <p className="muted" style={{ margin: "2px 0 8px", fontSize: 15 }}>
        {comparisonSubtitle(zone)}
      </p>
      <div className="slide-panel" data-dossier-panel={`n${idx}_figure`}>
        <NoviComparisonPanel curve={curve} zone={zone} />
      </div>
    </section>
  );
}

// Deck slide 1: one row per zone. Cells arrive pre-formatted from the
// backend (the same strings the .pptx table carries), so preview and
// deck can't drift; only the gap-flag colouring happens here.
function ZoneSummarySection({
  summary,
  error,
}: {
  summary: DossierZonesResponse | null;
  error: string | null;
}) {
  if (error) {
    return (
      <section style={{ marginTop: 16 }}>
        <h1 className="slide-title">Zone summary</h1>
        <p style={{ color: "#dc2626", fontSize: 14 }}>{error}</p>
      </section>
    );
  }
  if (!summary) return <p className="muted">loading zone summary…</p>;
  const flagCol: Record<number, "oil" | "gas"> = {
    [summary.headers.indexOf("Oil TC vs Novi")]: "oil",
    [summary.headers.indexOf("Gas TC vs Novi")]: "gas",
  };
  return (
    <section style={{ marginTop: 16 }}>
      <h1 className="slide-title">Zone summary</h1>
      <p className="muted" style={{ margin: "2px 0 6px", fontSize: 15 }}>
        which planned sticks take which type curve, the cohort that builds
        it, and the TC-vs-Novi read — the deck&apos;s first slide
      </p>
      <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            {summary.headers.map((h) => (
              <th
                key={h}
                style={{
                  border: "1px solid #e5e7eb",
                  background: "#f3f4f6",
                  padding: "3px 6px",
                  textAlign: "left",
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {summary.zones.map((z) => (
            <tr key={z.zone_name}>
              {z.cells.map((c, j) => {
                const stream = flagCol[j];
                const flagged = stream !== undefined && z.streams[stream].gap_flag;
                return (
                  <td
                    key={j}
                    style={{
                      border: "1px solid #e5e7eb",
                      padding: "3px 6px",
                      verticalAlign: "top",
                      color: flagged ? "#dc2626" : undefined,
                      fontWeight: flagged ? 700 : undefined,
                    }}
                  >
                    {c}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 13, maxWidth: 1160 }}>
        {summary.note}
      </p>
    </section>
  );
}

// Support slide title + subtitle (also the manifest strings). TC is
// the risked P50 the deck delivers; the well colours are each well's
// own anduin fit, unrisked — said explicitly so a risked TC sitting
// below its wells does not read as a defect.
function supportTitles(zone: DossierZone): { title: string; subtitle: string } {
  const fitted = zone.cohort
    .map((w) => w.oil_eur_per_ft)
    .filter((v): v is number => v !== null)
    .sort((a, b) => a - b);
  const n = fitted.length;
  const med = n === 0 ? null : n % 2 ? fitted[(n - 1) / 2]! : (fitted[n / 2 - 1]! + fitted[n / 2]!) / 2;
  const o = zone.streams.oil;
  const tc = o.eur_per_1000ft !== null ? (o.eur_per_1000ft / 1000).toFixed(1) : "—";
  const risk = o.risk_mult !== 1 ? `, risked ×${o.risk_mult}` : "";
  const scen = zone.n_scenarios === 1 ? "1 scenario" : `${zone.n_scenarios} scenarios`;
  return {
    title: `${zone.zone_name}: ${zone.n_sticks} ${zone.n_sticks === 1 ? "stick" : "sticks"} ← ${zone.cohort.length} curve wells`,
    subtitle:
      `type curve ${zone.curve_name} · TC oil ${tc} bbl/ft (P50${risk}) · curve wells median ${med !== null ? med.toFixed(1) : "—"} bbl/ft ` +
      `(${n} fitted, unrisked) · ${scen} · colour = anduin per-well oil EUR/ft, raw 50-yr`,
  };
}

// Preview-only legend under the live maps (the exported snapshots carry
// their own burned-in legend).
function SupportLegend({ color, range }: { color: string; range: { lo: number; hi: number } }) {
  return (
    <div className="muted" style={{ fontSize: 13, display: "flex", gap: 14, alignItems: "center", marginTop: 4 }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <span style={{ width: 22, height: 4, background: color, display: "inline-block" }} /> planned stick
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <span
          style={{
            width: 90, height: 8, display: "inline-block",
            background: "linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#fde725)",
          }}
        />
        curve well oil EUR {range.lo}–{range.hi} bbl/ft
      </span>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <span style={{ width: 22, height: 4, background: NO_FIT_COLOR, display: "inline-block" }} /> no anduin fit
      </span>
    </div>
  );
}

// The curve's wells (deck: the slides before its Oil/Gas/Water).
// A pinned oil Di is red; b at its 0.9/1.2 bound is common by design.
function CohortTable({
  table,
  headers,
  note,
}: {
  table: DossierCurveTable;
  headers: string[];
  note: string;
}) {
  const pinCol = headers.indexOf("Oil fit pinned");
  const cell = { border: "1px solid #e5e7eb", padding: "2px 6px", verticalAlign: "top" as const };
  return (
    <details open style={{ margin: "4px 0 10px" }}>
      <summary style={{ cursor: "pointer", fontSize: 15 }}>
        Curve wells — {table.rows.length} (nearest the planned sticks first)
      </summary>
      <table style={{ borderCollapse: "collapse", fontSize: 12, marginTop: 4 }}>
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h} style={{ ...cell, background: "#f3f4f6", textAlign: "left" }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={r[2]}>
              {r.map((c, j) => {
                const flagged = j === pinCol && c.includes("Di");
                return (
                  <td
                    key={j}
                    style={{ ...cell, color: flagged ? "#dc2626" : undefined, fontWeight: flagged ? 700 : undefined }}
                  >
                    {c}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12, maxWidth: 1160 }}>{note}</p>
    </details>
  );
}

const TD = { border: "1px solid #e5e7eb", padding: "2px 6px", verticalAlign: "top" as const };
const TH = { ...TD, background: "#f3f4f6", textAlign: "left" as const };

// Deck slide 2: per-zone x scenario EUR per well at the planned lateral.
function LateralSection({ summary }: { summary: DossierZonesResponse }) {
  const readCol = summary.lateral_headers.indexOf("Read");
  return (
    <section style={{ marginTop: 16 }}>
      <h1 className="slide-title">Lateral scaling</h1>
      <table style={{ borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>{summary.lateral_headers.map((h) => <th key={h} style={TH}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {summary.lateral_rows.map((r) => (
            <tr key={`${r.cells[0]}/${r.cells[1]}`}>
              {r.cells.map((c, j) => {
                const flagged = j === readCol && (r.extrapolated || r.thin);
                return (
                  <td key={j} style={{ ...TD, color: flagged ? "#dc2626" : undefined, fontWeight: flagged ? 700 : undefined }}>
                    {c}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 13, maxWidth: 1160 }}>{summary.lateral_note}</p>
    </section>
  );
}

// The curve's buildup funnel (deck: the slide after its well table).
function FunnelTables({ funnel }: { funnel: DossierFunnel }) {
  if (funnel.degraded) {
    return (
      <p className="muted" style={{ fontSize: 13 }}>
        Provenance not captured for this curve (saved before buildup capture) — no funnel; re-save the
        curve from the Type Curve tab to record it.
      </p>
    );
  }
  return (
    <details style={{ margin: "4px 0 10px" }}>
      <summary style={{ cursor: "pointer", fontSize: 15 }}>How the cohort was built</summary>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 4 }}>
        <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr>{["Stage", "What it removes", "Culled", "Remaining"].map((h) => <th key={h} style={TH}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {funnel.rows.map((r) => (
              <tr key={r[0]}>{r.map((c, j) => <td key={j} style={TD}>{c}</td>)}</tr>
            ))}
          </tbody>
        </table>
        <table style={{ borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr><th style={TH}>Criterion</th><th style={TH}>Value</th></tr>
          </thead>
          <tbody>
            {funnel.criteria.map(([k, v], i) => (
              <tr key={i}><td style={TD}>{k}</td><td style={TD}>{v}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
