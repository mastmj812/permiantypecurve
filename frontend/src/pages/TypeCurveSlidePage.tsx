// Single-page slide export for a saved type curve. Mounted directly
// by App when the URL hash is `#/type-curves/<id>/slide`, bypassing
// the normal app shell so print output is chrome-free.
//
// All fetches fan out via Promise.all on mount; the slide renders once
// every dependency resolves. Per-well curves can be ~30 round-trips
// for a typical cohort — fine for an export-time action.

import { useEffect, useMemo, useState } from "react";

import { type WellCurvesResponse, fetchWellCurves } from "../api/forecasts";
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
// SlideProbit is intentionally not imported — the probit panel was
// pulled on 2026-05-20 pending a design decision on whether the
// published P50 TC should match rate-aggregation or per-well-median
// EUR. The component file is kept so we can re-enable it later.
import { SlideRateChart } from "../components/slide/SlideRateChart";

interface Props {
  typeCurveId: string;
  compareWithId: string | null;
}

interface SlideData {
  curve: TypeCurveRow;
  previous: TypeCurveRow | null;
  wellStats: TypeCurveWellStat[];
  wellDetails: WellDetailLite[];
  wellCurves: WellCurvesResponse[];
}

export function TypeCurveSlidePage({ typeCurveId, compareWithId }: Props) {
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

        const api14s = curve.included_api14s ?? [];
        // Details power both the map (sh/bh lat-lon for fitBounds) and
        // the chart wrappers (lateral_ft per well for 10kft norm).
        const wellDetailsPromise =
          api14s.length > 0 ? fetchWellDetails(api14s) : Promise.resolve([]);
        const wellCurvesPromise = Promise.allSettled(
          api14s.map((a) => fetchWellCurves(a)),
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
  const lateralByApi14 = useMemo(() => {
    const m = new Map<string, number | null>();
    if (!data) return m;
    for (const s of data.wellStats) m.set(s.api14, s.lateral_ft);
    for (const d of data.wellDetails) {
      if (!m.has(d.api14) || m.get(d.api14) == null) m.set(d.api14, d.lateral_ft);
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

  return (
    <div className="slide-page">
      <h1 className="slide-title">
        {data.curve.name} <span className="slide-title-stream">Oil</span>
      </h1>
      <SlideParamTable current={data.curve} previous={data.previous} />
      {/* Layout: left column stacks Rate over Cum (240px each); right
          column is the Map spanning both rows (~492px tall to cover
          two 240-tall cells + the 12px grid gap). The probit panel
          was pulled — see import note above. */}
      <div className="slide-grid">
        <div className="slide-panel">
          <SlideRateChart
            current={data.curve}
            previous={data.previous}
            wellCurves={data.wellCurves}
            lateralByApi14={lateralByApi14}
          />
        </div>
        <div className="slide-panel slide-panel-map">
          <SlideMap
            api14s={data.curve.included_api14s ?? []}
            wellDetails={data.wellDetails}
            height={492}
          />
        </div>
        <div className="slide-panel">
          <SlideCumChart
            current={data.curve}
            previous={data.previous}
            wellCurves={data.wellCurves}
            lateralByApi14={lateralByApi14}
          />
        </div>
      </div>
    </div>
  );
}
