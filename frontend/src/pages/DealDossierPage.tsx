// Deal dossier preview + export. Mounted chrome-free at
// `#/deals/<id>/dossier` (like the type-curve slide page). Renders the
// deal's SAVED Blue Ox config: one section per pinned narvi scenario
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
  exportDealDossierPptx,
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
import { ScenarioGunBarrel } from "../components/dossier/ScenarioGunBarrel";
import { ScenarioSlideMap } from "../components/dossier/ScenarioSlideMap";
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
  const [curvesReady, setCurvesReady] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

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
    const geoms: unknown[] = [];
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
      }
    }
    return {
      wells,
      colors,
      aoi: geoms.length
        ? JSON.stringify({ type: "GeometryCollection", geometries: geoms })
        : null,
    };
  }, [scenarios, cfg]);

  const allReady =
    scenarios !== null && curveIds.every((_, i) => curvesReady.has(i));

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
        <span className="muted" style={{ fontSize: 11, marginLeft: 12 }}>
          maps are live — drag / scroll-zoom to frame each shot; the export
          captures the current views
        </span>
        {error && <span style={{ color: "#dc2626", fontSize: 12, marginLeft: 12 }}>{error}</span>}
        {notice && !error && (
          <span style={{ color: "#15803d", fontSize: 12, marginLeft: 12 }}>{notice}</span>
        )}
      </div>

      {scenarios === null && <p className="muted">loading narvi scenarios…</p>}

      {overview && (
        <section style={{ marginTop: 16 }}>
          <h1 className="slide-title">Curve assignment overview</h1>
          <p className="muted" style={{ margin: "2px 0 6px", fontSize: 13 }}>
            every selected scenario on one map — planned wells colored by
            the type curve their zone assigns under the saved config; gray
            = PDP context or a well no zone captures
          </p>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", margin: "0 0 6px", fontSize: 12 }}>
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
          <div className="slide-panel">
            <ScenarioSlideMap
              aoiGeojson={overview.aoi}
              wells={overview.wells}
              width={1160}
              height={520}
              lazy
              colorForWell={(w) => overview.colors.get(w) ?? UNASSIGNED_COLOR}
            />
          </div>
        </section>
      )}

      {scenarios?.map((sd, i) => (
        <section key={`${sd.deal_id}/${sd.scenario_id}`} style={{ marginTop: 20 }}>
          <h1 className="slide-title">{sd.name ?? sd.scenario_id}</h1>
          <p className="muted" style={{ margin: "2px 0 8px", fontSize: 13 }}>
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
          onReady={() =>
            setCurvesReady((prev) => new Set(prev).add(i))
          }
        />
      ))}
    </div>
  );
}

interface CurveSectionProps {
  curveId: string;
  idx: number;
  onReady: () => void;
}

interface CurveData {
  curve: TypeCurveRow;
  wellStats: TypeCurveWellStat[];
  wellDetails: WellDetailLite[];
  wellCurves: WellCurvesResponse[];
}

// One type curve's dossier section — the same data + panels as the
// slide export page (TypeCurveSlidePage), all streams stacked visibly.
function DossierCurveSection({ curveId, idx, onReady }: CurveSectionProps) {
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
      <SlideParamTable current={data.curve} previous={null} />
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 8 }}>
        <div className="slide-panel slide-panel-map" data-dossier-panel={`c${idx}_map`}>
          <SlideMap
            api10s={api10s}
            wellDetails={data.wellDetails}
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
