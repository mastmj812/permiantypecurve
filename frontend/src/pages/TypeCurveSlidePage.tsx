// Single-page slide export for a saved type curve. Mounted directly
// by App when the URL hash is `#/type-curves/<id>/slide`, bypassing
// the normal app shell so print output is chrome-free.
//
// All fetches fan out via Promise.all on mount; the slide renders once
// every dependency resolves. Per-well curves can be ~30 round-trips
// for a typical cohort — fine for an export-time action.

import { useEffect, useMemo, useState } from "react";

import { type Stream, type WellCurvesResponse, fetchWellCurves } from "../api/forecasts";
import {
  type TypeCurveRow,
  type TypeCurveWellStat,
  fetchTypeCurve,
  fetchTypeCurveWellStats,
} from "../api/typeCurves";
import { type WellDetailLite, fetchWellDetails } from "../api/wells";

import { SlideCumChart } from "../components/slide/SlideCumChart";
import { SlideMap } from "../components/slide/SlideMap";
import { SlideParamTable } from "../components/slide/SlideParamTable";
import { SlideRateChart } from "../components/slide/SlideRateChart";
import { TypeCurveProbit } from "../type_curves/TypeCurveProbit";

interface Props {
  typeCurveId: string;
  compareWithId: string | null;
  includeProbit?: boolean;
}

interface SlideData {
  curve: TypeCurveRow;
  previous: TypeCurveRow | null;
  wellStats: TypeCurveWellStat[];
  wellDetails: WellDetailLite[];
  wellCurves: WellCurvesResponse[];
}

export function TypeCurveSlidePage({
  typeCurveId,
  compareWithId,
  includeProbit = false,
}: Props) {
  const [data, setData] = useState<SlideData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Remove the app's default body margin / background so the slide
    // sits flush to the page edge. Cleanup restores them when the page
    // unmounts (in practice this slide lives in its own tab, but the
    // cleanup is harmless either way).
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
    (async () => {
      try {
        const [curve, previousMaybe, wellStats] = await Promise.all([
          fetchTypeCurve(typeCurveId),
          compareWithId
            ? fetchTypeCurve(compareWithId).catch(() => null)
            : Promise.resolve(null),
          fetchTypeCurveWellStats(typeCurveId),
        ]);
        if (cancelled) return;

        const api10s = curve.included_api10s ?? [];
        // Details power both the map (sh/bh lat-lon for fitBounds) and
        // the chart wrappers (lateral_ft per well for 10kft norm).
        const wellDetailsPromise =
          api10s.length > 0 ? fetchWellDetails(api10s) : Promise.resolve([]);
        const wellCurvesPromise = Promise.allSettled(
          api10s.map((a) => fetchWellCurves(a)),
        );
        const [wellDetails, wellCurvesSettled] = await Promise.all([
          wellDetailsPromise,
          wellCurvesPromise,
        ]);
        if (cancelled) return;

        const wellCurves: WellCurvesResponse[] = [];
        for (const r of wellCurvesSettled) {
          if (r.status === "fulfilled") wellCurves.push(r.value);
        }

        setData({
          curve,
          previous: previousMaybe,
          wellStats,
          wellDetails,
          wellCurves,
        });
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [typeCurveId, compareWithId]);

  // Lateral lookup is shared across rate + cum charts. Prefer the
  // wellStats entry (single SQL with the curve's snapshot) and fall
  // back to wellDetails (richer payload, used by the map).
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
      <div className="slide-page slide-error">
        <h2>Couldn't load slide</h2>
        <p>{error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="slide-page slide-loading">
        <p>Building slide…</p>
      </div>
    );
  }

  const STREAMS: Stream[] = ["oil", "gas", "water"];
  const api10s = data.curve.included_api10s ?? [];
  // Map dimensions match the placement in PowerPoint so the captured
  // PNG aspect matches the picture box and PowerPoint doesn't have
  // to stretch / letterbox.
  //   probit off → 6.93" × 4.34" (~665 × 418 px)
  //   probit on  → 5.42" × 2.16" (~520 × 207 px)
  const mapWidth = includeProbit ? 520 : 665;
  const mapHeight = includeProbit ? 207 : 418;

  // Reusable per-stream panel block. Used for the visible oil section
  // and for the off-screen gas/water sections; only the layout changes.
  const renderStreamPanels = (stream: Stream, withMap: boolean) => (
    <>
      <div className="slide-panel" data-slide-panel-rate data-stream={stream}>
        <SlideRateChart
          current={data.curve}
          previous={data.previous}
          wellCurves={data.wellCurves}
          lateralByApi10={lateralByApi10}
          stream={stream}
        />
      </div>
      {withMap && (
        <div className="slide-panel slide-panel-map" data-slide-panel-map>
          <SlideMap
            api10s={api10s}
            wellDetails={data.wellDetails}
            width={mapWidth}
            height={mapHeight}
          />
        </div>
      )}
      <div className="slide-panel" data-slide-panel-cum data-stream={stream}>
        <SlideCumChart
          current={data.curve}
          previous={data.previous}
          wellCurves={data.wellCurves}
          lateralByApi10={lateralByApi10}
          stream={stream}
        />
      </div>
      {includeProbit && (
        <div className="slide-panel" data-slide-panel-probit data-stream={stream}>
          <TypeCurveProbit
            api10s={api10s}
            tcEurPerUnit={
              (data.curve.series as { streams?: Record<string, { fitted?: { eur_per_unit?: number } | null }> }).streams?.[stream]?.fitted?.eur_per_unit ?? null
            }
            prevEurPerUnit={
              data.previous
                ? (data.previous.series as { streams?: Record<string, { fitted?: { eur_per_unit?: number } | null }> }).streams?.[stream]?.fitted?.eur_per_unit ?? null
                : null
            }
            prevLabel={data.previous?.name ?? null}
            stream={stream}
            width={520}
            height={207}
          />
        </div>
      )}
    </>
  );

  return (
    <div className="slide-page">
      {/* Visible (oil) section. The PPTX export captures rate/cum
          (+ optional probit) from this section AND from the off-
          screen gas/water sections below. The map is captured once
          from here and the backend uses it for all 3 stream slides. */}
      <h1 className="slide-title">
        {data.curve.name} <span className="slide-title-stream">Oil</span>
      </h1>
      <SlideParamTable current={data.curve} previous={data.previous} />
      <div
        className={`slide-grid${includeProbit ? " slide-grid-with-probit" : ""}`}
        data-slide-section="oil"
      >
        {renderStreamPanels("oil", true)}
      </div>

      {/* Off-screen gas + water variants. Rendered in the DOM so the
          capture util can snapshot their SVGs, but positioned far
          off-canvas so they don't visually clutter the iframe. Map
          isn't re-rendered here — the oil section's map is reused
          across all three PPT stream slides. */}
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          left: -99999,
          top: 0,
          width: "max-content",
          pointerEvents: "none",
        }}
      >
        {STREAMS.filter((s) => s !== "oil").map((stream) => (
          <div key={stream} data-slide-section={stream}>
            {renderStreamPanels(stream, false)}
          </div>
        ))}
      </div>
    </div>
  );
}
