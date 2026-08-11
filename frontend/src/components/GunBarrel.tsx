// GunBarrel — cross-section view of the staged wells, oriented
// perpendicular to the cohort's mean lateral azimuth. Each well
// projects to a single point: X = perpendicular offset from the cohort
// centroid (ft), Y = TVD (ft, inverted so deeper draws lower). Solid
// circles for known TVD; open circles for wells whose TVD is missing
// (placed at the cohort mean as a fallback so they still show up).
//
// The user clicks circles to toggle whether each well will be added to
// the cohort when they hit "Add N selected" in the inspect modal.
// Hover surfaces a small tooltip with the bits that matter for
// parent/child + formation-validation decisions.
//
// Pure SVG, no charting library — same approach as DeclineChart.

import { useId, useMemo, useRef, useState } from "react";

import type { WellDetailLite } from "../api/wells";
import { colorForBlueox } from "../map/formations";
import { COHORT_HALO_COLOR } from "../map/wellsLayers";

export interface GunBarrelProps {
  wells: WellDetailLite[];
  // Nearby wells rendered greyed + non-selectable for co-development
  // context (e.g. a BS2_C bench right above BS2_S candidates). They
  // project into the STAGED wells' frame — they never shift the
  // centroid/azimuth, never respond to clicks or box-select, and never
  // enter the staged selection. Hover tooltip only.
  contextWells?: WellDetailLite[];
  // api10s of wells that will be added when the user commits. The
  // unchecked wells render at lowered opacity. Toggle via onToggle.
  selectedApi10s: Set<string>;
  // api10s of wells ALREADY in the active cohort. These get a sky-blue
  // halo behind the circle — the same "this well is in the cohort" cue
  // the map paints on cohort sticks. Distinct from selectedApi10s
  // (staging intent). Empty/undefined outside the modal.
  cohortApi10s?: Set<string>;
  onToggle: (api10: string) => void;
  // Rubber-band box select. Drag across empty chart background to select
  // the enclosed wells. mode: plain drag replaces the selection, Shift
  // adds, Alt removes. Omit to disable box select (e.g. reuse outside
  // the modal). Individual circle clicks still toggle via onToggle.
  onBoxSelect?: (
    api10s: string[],
    mode: "replace" | "add" | "subtract",
  ) => void;
  // Shared hover state lifted into the InspectModal — lets the
  // production charts and the gun-barrel highlight the same well at
  // the same time. Both undefined when this component is reused
  // outside the modal.
  hoveredApi10?: string | null;
  onHover?: (api10: string | null) => void;
  width?: number;
  height?: number;
}

const PAD = { top: 24, right: 32, bottom: 40, left: 56 };
const WELL_RADIUS = 7;
// Rough lat/lon → feet at Permian latitude (~32°N). One degree of lat
// is ~364,000 ft anywhere; one degree of lon shrinks with latitude.
// Inline rather than pulling a geo lib — same trick the retired
// synthetic client used (and Phase 2 retired alongside it).
const FT_PER_DEG_LAT = 364_000;

// ---------------- math helpers ----------------

interface Projected {
  api10: string;
  offsetFt: number;
  tvd: number;
  tvdEstimated: boolean;
  well: WellDetailLite;
}

// The projection frame: centroid + perpendicular axis + scaling, all
// derived from the STAGED wells only. Context wells project INTO this
// frame (projectIntoFrame) so toggling them on can never move a staged
// well's offset — the frame is the staged cohort's story.
interface Frame {
  cLon: number;
  cLat: number;
  perpX: number;
  perpY: number;
  ftPerDegLon: number;
  meanTvd: number;
  axisBearingDeg: number;
}

function usableWells(wells: WellDetailLite[]): WellDetailLite[] {
  // Wells with no surface OR no bottomhole coordinates are dropped
  // entirely — we can't place them on the cross-section without a
  // lateral vector. (TVD missing is OK; we estimate. Coords missing
  // is not.)
  return wells.filter(
    (w) =>
      w.sh_lat != null && w.sh_lon != null &&
      w.bh_lat != null && w.bh_lon != null,
  );
}

function computeFrame(usable: WellDetailLite[]): Frame | null {
  if (usable.length === 0) return null;

  // Cohort centroid in lon/lat (mean of lateral midpoints).
  let cLon = 0, cLat = 0;
  for (const w of usable) {
    cLon += ((w.sh_lon! + w.bh_lon!) / 2);
    cLat += ((w.sh_lat! + w.bh_lat!) / 2);
  }
  cLon /= usable.length;
  cLat /= usable.length;

  // Mean azimuth via vector average of normalized sh→bh vectors.
  // Vector mean avoids the 0°/360° wrap-around that would bite a
  // naive `mean(atan2(...))`.
  let mx = 0, my = 0;
  for (const w of usable) {
    const dx = w.bh_lon! - w.sh_lon!;
    const dy = w.bh_lat! - w.sh_lat!;
    const len = Math.hypot(dx, dy);
    if (len > 0) {
      mx += dx / len;
      my += dy / len;
    }
  }
  // Unit vector pointing along the mean lateral direction.
  const lateralLen = Math.hypot(mx, my) || 1;
  const lateralX = mx / lateralLen;
  const lateralY = my / lateralLen;
  // Perpendicular vector (90° rotation). Canonicalize +X to point East
  // — or South when the lateral runs E-W and the section is N-S — so the
  // cross-section defaults to a West→East read. This matches the
  // erebor/narvi "+offset = E/S" convention; the flip toggle in the
  // component mirrors it when a given pad reads better the other way.
  let perpX = -lateralY;
  let perpY = lateralX;
  const EPS = 1e-9;
  if (perpX < -EPS || (Math.abs(perpX) <= EPS && perpY > 0)) {
    perpX = -perpX;
    perpY = -perpY;
  }

  // Lat/lon → ft scaling at the cohort centroid latitude.
  const ftPerDegLon = Math.cos((cLat * Math.PI) / 180) * FT_PER_DEG_LAT;

  // TVD fallback: mean of known TVDs across the cohort. Missing-TVD
  // wells get this value + an "estimated" flag for the renderer.
  const knownTvds = usable
    .map((w) => w.tvd_ft)
    .filter((v): v is number => v != null && Number.isFinite(v));
  const meanTvd =
    knownTvds.length > 0
      ? knownTvds.reduce((a, b) => a + b, 0) / knownTvds.length
      : 10_000;

  // Compass bearing for +X. Convert perpendicular unit vector to
  // bearing degrees from north (0 = N, 90 = E).
  const bearingRad = Math.atan2(perpX, perpY);
  const axisBearingDeg = ((bearingRad * 180) / Math.PI + 360) % 360;

  return { cLon, cLat, perpX, perpY, ftPerDegLon, meanTvd, axisBearingDeg };
}

function projectIntoFrame(
  wells: WellDetailLite[],
  frame: Frame,
): Projected[] {
  return usableWells(wells).map((w) => {
    const midLon = (w.sh_lon! + w.bh_lon!) / 2;
    const midLat = (w.sh_lat! + w.bh_lat!) / 2;
    // Offset vector from cohort centroid, in feet.
    const dxFt = (midLon - frame.cLon) * frame.ftPerDegLon;
    const dyFt = (midLat - frame.cLat) * FT_PER_DEG_LAT;
    // Signed projection onto the perpendicular axis.
    const offsetFt = dxFt * frame.perpX + dyFt * frame.perpY;
    const tvdKnown = w.tvd_ft != null && Number.isFinite(w.tvd_ft);
    return {
      api10: w.api10,
      offsetFt,
      tvd: tvdKnown ? w.tvd_ft! : frame.meanTvd,
      tvdEstimated: !tvdKnown,
      well: w,
    };
  });
}

function projectWells(wells: WellDetailLite[]): {
  projected: Projected[];
  // Compass bearing (deg from N) of the +X axis BEFORE any user flip.
  // The component turns this into a label and mirrors it when flipped.
  axisBearingDeg: number;
  frame: Frame | null;
} {
  const frame = computeFrame(usableWells(wells));
  if (frame === null) {
    return { projected: [], axisBearingDeg: 90, frame: null };
  }
  return {
    projected: projectIntoFrame(wells, frame),
    axisBearingDeg: frame.axisBearingDeg,
    frame,
  };
}

function compassLabel(deg: number): string {
  // 16-point compass — enough granularity for the cross-section
  // direction note, not so much it looks pedantic.
  const dirs = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
  ];
  const idx = Math.round(deg / 22.5) % 16;
  return dirs[idx]!;
}

// ---------------- component ----------------

const CONTEXT_COLOR = "#9ca3af";
const CONTEXT_RADIUS = 5;

export function GunBarrel({
  wells,
  contextWells,
  selectedApi10s,
  cohortApi10s,
  onToggle,
  onBoxSelect,
  hoveredApi10 = null,
  onHover,
  width = 880,
  height = 320,
}: GunBarrelProps) {
  const { projected, axisBearingDeg, frame } = useMemo(
    () => projectWells(wells),
    [wells],
  );

  // Context wells project into the STAGED frame — never into their own.
  // Staged duplicates are dropped defensively (the endpoint already
  // excludes the inputs), as are context wells with no real TVD: a
  // neighbor plotted at the staged cohort's mean depth would invent
  // exactly the above/below relationship this view exists to show.
  const contextProjected = useMemo(() => {
    if (!frame || !contextWells || contextWells.length === 0) return [];
    const stagedSet = new Set(wells.map((w) => w.api10));
    return projectIntoFrame(
      contextWells.filter(
        (w) =>
          !stagedSet.has(w.api10) &&
          w.tvd_ft != null &&
          Number.isFinite(w.tvd_ft),
      ),
      frame,
    );
  }, [contextWells, frame, wells]);
  // Local hover keeps the projected x/y for tooltip positioning. The
  // *which-well-is-hovered* truth lives at hoveredApi10 (the lifted
  // prop) so the production charts can react to the same hover.
  const [hover, setHover] = useState<Projected | null>(null);

  // Rubber-band box select. svgRef converts client coords → svg-pixel
  // coords (no viewBox, so 1 svg unit == 1 px); selBox holds the live
  // drag rectangle in those coords (null when not dragging).
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [selBox, setSelBox] = useState<{
    x0: number;
    y0: number;
    x1: number;
    y1: number;
  } | null>(null);

  // TVD-axis zoom: drag a window on the y-axis gutter to zoom the depth
  // range (one deep outlier otherwise compresses the shallow benches
  // into an unreadable band). Double-click the gutter — or the reset
  // pill — restores auto-fit. yZoom is in TVD feet; zoomBrush is the
  // live drag band in svg pixels.
  const [yZoom, setYZoom] = useState<{ min: number; max: number } | null>(null);
  const [zoomBrush, setZoomBrush] = useState<{ y0: number; y1: number } | null>(
    null,
  );
  // Clip circles to the plot rect so zoomed-out wells don't spill over
  // the axes. useId keeps the clipPath id unique per mounted instance.
  const clipId = useId();

  // Orientation flip. The projection canonicalizes +X to East (typical
  // West→East read), but section geometry varies pad-to-pad and the
  // engineer sometimes wants it mirrored. flip = true mirrors the
  // x-axis: sgn negates every plotted offset and the axis label swaps
  // to the opposite compass point. Same behavior shipped in erebor/narvi.
  const [flip, setFlip] = useState<boolean>(false);
  const sgn = flip ? -1 : 1;
  // Right end of the axis is +X (== axisLabel); left end is its opposite.
  const labelBearing = (axisBearingDeg + (flip ? 180 : 0)) % 360;
  const axisLabel = compassLabel(labelBearing);
  const leftLabel = compassLabel((labelBearing + 180) % 360);

  const plotArea = {
    x: PAD.left,
    y: PAD.top,
    w: width - PAD.left - PAD.right,
    h: height - PAD.top - PAD.bottom,
  };

  // Auto-fit axes. Pad x range by 10% per side so circles at the
  // extremes aren't clipped by the chart border. Pad y likewise.
  // Empty-cohort guard: render an explanatory message rather than NaN.
  if (projected.length === 0) {
    return (
      <svg
        width={width}
        height={height}
        className="gun-barrel-svg"
        role="img"
      >
        <text
          x={width / 2}
          y={height / 2}
          textAnchor="middle"
          fontSize="12"
          fill="#6b7280"
        >
          No wells with surface + bottomhole coordinates to render.
        </text>
      </svg>
    );
  }

  // Plot in display space: sgn folds the orientation flip into the
  // offset up front so the auto-fit range, ticks, and circle positions
  // all mirror together. Raw p.offsetFt stays canonical for tooltips.
  // Context wells join the auto-fit so an adjacent bench above/below is
  // actually visible — the frame itself stays staged-only.
  const fitPoints = [...projected, ...contextProjected];
  const offsets = fitPoints.map((p) => sgn * p.offsetFt);
  const tvds = fitPoints.map((p) => p.tvd);
  const xMin0 = Math.min(...offsets);
  const xMax0 = Math.max(...offsets);
  // Always give the chart at least 1000 ft of x range so a tight pad
  // of co-located wells still renders with breathing room. Avoids the
  // "single dot in the middle" degenerate case.
  const xSpan = Math.max(xMax0 - xMin0, 1000);
  const xPad = xSpan * 0.1;
  const xMin = xMin0 - xPad;
  const xMax = xMax0 + xPad;
  const xRange = xMax - xMin;

  const yMin0 = Math.min(...tvds);
  const yMax0 = Math.max(...tvds);
  const ySpan = Math.max(yMax0 - yMin0, 200);
  const yPad = ySpan * 0.15;
  // Manual TVD zoom overrides auto-fit; auto-fit stays the default.
  const yMin = yZoom ? yZoom.min : yMin0 - yPad;
  const yMax = yZoom ? yZoom.max : yMax0 + yPad;
  const yRange = yMax - yMin;

  // X: left→right = -offset → +offset. Y inverted: shallower at top.
  const xScale = (v: number) =>
    plotArea.x + ((v - xMin) / xRange) * plotArea.w;
  const yScale = (v: number) =>
    plotArea.y + ((v - yMin) / yRange) * plotArea.h;

  const xTicks = niceTicks(xMin, xMax, 6);
  const yTicks = niceTicks(yMin, yMax, 5);

  // Drag on the y-axis gutter → zoom to that TVD window. The gutter
  // rect stops propagation, so this never collides with box-select.
  function onAxisMouseDown(e: React.MouseEvent<SVGRectElement>) {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    e.preventDefault();
    e.stopPropagation();
    const startY = e.clientY - rect.top;
    // Freeze the at-drag-start scale so the window converts against the
    // domain the user was looking at, even mid-re-render.
    const y0 = plotArea.y;
    const h = plotArea.h;
    const dMin = yMin;
    const dRange = yRange;
    setZoomBrush({ y0: startY, y1: startY });
    const clampPy = (py: number) => Math.min(Math.max(py, y0), y0 + h);
    const onMove = (ev: MouseEvent) => {
      setZoomBrush({ y0: startY, y1: ev.clientY - rect.top });
    };
    const onUp = (ev: MouseEvent) => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setZoomBrush(null);
      const endY = ev.clientY - rect.top;
      if (Math.abs(endY - startY) < 8) return; // click, not a window
      const tvdAt = (py: number) => dMin + ((clampPy(py) - y0) / h) * dRange;
      const a = tvdAt(startY);
      const b = tvdAt(endY);
      setYZoom({ min: Math.min(a, b), max: Math.max(a, b) });
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  // Start a rubber-band box on mousedown over empty chart background.
  // Mousedown on a well circle is left to that circle's onClick (toggle).
  // Modifier at drag start fixes the mode for the whole drag.
  function onSvgMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    if (!onBoxSelect) return;
    if ((e.target as Element).tagName === "circle") return;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const startX = e.clientX - rect.left;
    const startY = e.clientY - rect.top;
    // Reserve the y-axis gutter for the zoom brush.
    if (startX < plotArea.x) return;
    const mode: "replace" | "add" | "subtract" = e.shiftKey
      ? "add"
      : e.altKey
        ? "subtract"
        : "replace";
    e.preventDefault();

    const onMove = (ev: MouseEvent) => {
      setSelBox({
        x0: startX,
        y0: startY,
        x1: ev.clientX - rect.left,
        y1: ev.clientY - rect.top,
      });
    };
    const onUp = (ev: MouseEvent) => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setSelBox(null);
      const endX = ev.clientX - rect.left;
      const endY = ev.clientY - rect.top;
      // Ignore a click-sized drag on background so a stray click doesn't
      // wipe the whole selection to empty (replace mode).
      if (Math.abs(endX - startX) < 4 && Math.abs(endY - startY) < 4) return;
      const xa = Math.min(startX, endX);
      const xb = Math.max(startX, endX);
      const ya = Math.min(startY, endY);
      const yb = Math.max(startY, endY);
      const hit: string[] = [];
      for (const p of projected) {
        const cx = xScale(sgn * p.offsetFt);
        const cy = yScale(p.tvd);
        // A well zoomed out of the visible TVD window is clipped from
        // view — an oversized box must not silently grab it.
        if (cy < plotArea.y || cy > plotArea.y + plotArea.h) continue;
        if (cx >= xa && cx <= xb && cy >= ya && cy <= yb) hit.push(p.api10);
      }
      onBoxSelect(hit, mode);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  return (
    <div className="gun-barrel-wrap">
      <button
        type="button"
        className="gun-barrel-flip-btn"
        onClick={() => setFlip((f) => !f)}
        title="Flip orientation — mirror the cross-section's x-axis"
        aria-pressed={flip}
      >
        ⇋ {leftLabel}→{axisLabel}
      </button>
      {yZoom && (
        <button
          type="button"
          className="gun-barrel-flip-btn"
          style={{ right: "auto", left: 6 }}
          onClick={() => setYZoom(null)}
          title="Reset the TVD zoom to auto-fit (or double-click the y-axis)"
        >
          ↕ reset {Math.round(yZoom.min).toLocaleString()}–
          {Math.round(yZoom.max).toLocaleString()} ft
        </button>
      )}
      <svg
        ref={svgRef}
        width={width}
        height={height}
        className="gun-barrel-svg"
        role="img"
        aria-label="Gun-barrel cross-section of staged wells"
        onMouseDown={onSvgMouseDown}
        style={{ cursor: onBoxSelect ? "crosshair" : undefined }}
      >
      {/* Plot frame */}
      <rect
        x={plotArea.x}
        y={plotArea.y}
        width={plotArea.w}
        height={plotArea.h}
        fill="#fff"
        stroke="#e5e7eb"
      />

      {/* Clip for well circles: a zoomed TVD window pushes out-of-range
          wells outside the plot — clipping hides them AND removes their
          pointer targets (clip-path affects hit-testing). */}
      <defs>
        <clipPath id={clipId}>
          <rect
            x={plotArea.x}
            y={plotArea.y}
            width={plotArea.w}
            height={plotArea.h}
          />
        </clipPath>
      </defs>

      {/* Y grid + tick labels (TVD, ft) */}
      {yTicks.map((t) => (
        <g key={`y${t}`}>
          <line
            x1={plotArea.x}
            x2={plotArea.x + plotArea.w}
            y1={yScale(t)}
            y2={yScale(t)}
            stroke="#f3f4f6"
          />
          <text
            x={plotArea.x - 6}
            y={yScale(t)}
            textAnchor="end"
            dominantBaseline="middle"
            fontSize="10"
            fill="#6b7280"
          >
            {Math.round(t).toLocaleString()}
          </text>
        </g>
      ))}

      {/* X grid + tick labels (offset, ft) */}
      {xTicks.map((t) => (
        <g key={`x${t}`}>
          <line
            x1={xScale(t)}
            x2={xScale(t)}
            y1={plotArea.y}
            y2={plotArea.y + plotArea.h}
            stroke="#f3f4f6"
          />
          <text
            x={xScale(t)}
            y={plotArea.y + plotArea.h + 14}
            textAnchor="middle"
            fontSize="10"
            fill="#6b7280"
          >
            {Math.round(t).toLocaleString()}
          </text>
        </g>
      ))}

      {/* Circles clipped to the plot rect (see clipPath above). */}
      <g clipPath={`url(#${clipId})`}>
      {/* Context wells — greyed neighbors (any formation, filters
          ignored) projected into the staged frame. Display-only: no
          toggle, no box-select, no halo, no selection count. Hover
          shows the standard tooltip — formation + TVD is the value
          ("is there a BS2_C right above these?"). Drawn first so the
          staged circles always paint on top. */}
      {contextProjected.map((p) => (
        <circle
          key={`ctx-${p.api10}`}
          cx={xScale(sgn * p.offsetFt)}
          cy={yScale(p.tvd)}
          r={CONTEXT_RADIUS}
          fill={CONTEXT_COLOR}
          stroke={CONTEXT_COLOR}
          strokeWidth={1}
          opacity={0.35}
          onMouseEnter={() => setHover(p)}
          onMouseLeave={() =>
            setHover((h) => (h?.api10 === p.api10 ? null : h))
          }
        />
      ))}

      {/* Cohort-membership halos — sky-blue disc under any well already
          in the active cohort, matching the map's cohort stick halo.
          Drawn before the well circles so they sit underneath. Held at a
          steady opacity (it's a membership badge, not a selection/hover
          cue), but dimmed for deselected wells so a ghosted circle isn't
          crowned by a bright halo. */}
      {cohortApi10s &&
        projected.map((p) =>
          cohortApi10s.has(p.api10) ? (
            <circle
              key={`halo-${p.api10}`}
              cx={xScale(sgn * p.offsetFt)}
              cy={yScale(p.tvd)}
              r={WELL_RADIUS + 4}
              fill={COHORT_HALO_COLOR}
              opacity={selectedApi10s.has(p.api10) ? 0.5 : 0.2}
              pointerEvents="none"
            />
          ) : null,
        )}

      {/* Well circles. Hovered well stays at full opacity + thicker
          stroke; other selected wells fade slightly so the highlight
          reads. Deselected wells stay ghosted regardless of hover. */}
      {projected.map((p) => {
        const color = colorForBlueox(p.well.basin_blueox, p.well.formation_blueox);
        const selected = selectedApi10s.has(p.api10);
        const isHovered = hoveredApi10 === p.api10;
        const anyHover = hoveredApi10 != null;
        const opacity = !selected
          ? 0.35
          : anyHover && !isHovered
            ? 0.4
            : 1;
        return (
          <circle
            key={p.api10}
            cx={xScale(sgn * p.offsetFt)}
            cy={yScale(p.tvd)}
            r={WELL_RADIUS}
            className="gun-barrel-well-circle"
            fill={p.tvdEstimated ? "none" : color}
            stroke={color}
            strokeWidth={isHovered ? 2.5 : 1.5}
            opacity={opacity}
            style={{ cursor: "pointer" }}
            onClick={() => onToggle(p.api10)}
            onMouseEnter={() => {
              setHover(p);
              onHover?.(p.api10);
            }}
            onMouseLeave={() => {
              setHover((h) => (h?.api10 === p.api10 ? null : h));
              onHover?.(null);
            }}
          />
        );
      })}
      </g>

      {/* Y-axis zoom gutter: drag a TVD window to zoom, double-click to
          reset. Sits over the tick labels left of the plot. */}
      <rect
        x={0}
        y={plotArea.y}
        width={plotArea.x}
        height={plotArea.h}
        fill="transparent"
        style={{ cursor: "ns-resize" }}
        onMouseDown={onAxisMouseDown}
        onDoubleClick={() => setYZoom(null)}
      >
        <title>drag to zoom TVD · double-click to reset</title>
      </rect>

      {/* Live zoom-brush band while dragging on the axis. */}
      {zoomBrush && (
        <rect
          x={plotArea.x}
          y={Math.max(plotArea.y, Math.min(zoomBrush.y0, zoomBrush.y1))}
          width={plotArea.w}
          height={Math.min(
            plotArea.y + plotArea.h,
            Math.max(zoomBrush.y0, zoomBrush.y1),
          ) - Math.max(plotArea.y, Math.min(zoomBrush.y0, zoomBrush.y1))}
          fill="rgba(37,99,235,0.08)"
          stroke="#2563eb"
          strokeWidth={1}
          strokeDasharray="4 3"
          pointerEvents="none"
        />
      )}

      {/* Axis labels */}
      <text
        x={plotArea.x + plotArea.w / 2}
        y={height - 6}
        textAnchor="middle"
        fontSize="11"
        fill="#374151"
      >
        Cross-section offset (ft) — +X is {axisLabel}
      </text>
      <text
        x={14}
        y={plotArea.y + plotArea.h / 2}
        transform={`rotate(-90 14 ${plotArea.y + plotArea.h / 2})`}
        textAnchor="middle"
        fontSize="11"
        fill="#374151"
      >
        TVD (ft)
      </text>

      {/* Rubber-band selection rectangle while dragging. */}
      {selBox && (
        <rect
          x={Math.min(selBox.x0, selBox.x1)}
          y={Math.min(selBox.y0, selBox.y1)}
          width={Math.abs(selBox.x1 - selBox.x0)}
          height={Math.abs(selBox.y1 - selBox.y0)}
          fill="rgba(37,99,235,0.10)"
          stroke="#2563eb"
          strokeWidth={1}
          strokeDasharray="4 3"
          pointerEvents="none"
        />
      )}

      {/* Tooltip — rendered last so it sits on top of the well circles */}
      {hover && (
        <GunBarrelTooltip
          point={hover}
          x={xScale(sgn * hover.offsetFt)}
          y={yScale(hover.tvd)}
          chartWidth={width}
          chartHeight={height}
        />
      )}
      </svg>
    </div>
  );
}

// ---------------- tooltip ----------------

function GunBarrelTooltip({
  point,
  x,
  y,
  chartWidth,
  chartHeight,
}: {
  point: Projected;
  x: number;
  y: number;
  chartWidth: number;
  chartHeight: number;
}) {
  const w = point.well;
  // Position the tooltip to the right of the circle by default; flip
  // to the left if it'd run off the chart's right edge. Same trick for
  // top/bottom so a circle near the y-axis edges doesn't push the box
  // off-screen.
  const TT_WIDTH = 220;
  const TT_HEIGHT = 96;
  const placeLeft = x + WELL_RADIUS + 8 + TT_WIDTH > chartWidth;
  const placeAbove = y + TT_HEIGHT + 8 > chartHeight;
  const tx = placeLeft ? x - WELL_RADIUS - 8 - TT_WIDTH : x + WELL_RADIUS + 8;
  const ty = placeAbove ? y - TT_HEIGHT - 4 : y + 4;

  return (
    <g pointerEvents="none">
      <rect
        x={tx}
        y={ty}
        width={TT_WIDTH}
        height={TT_HEIGHT}
        fill="rgba(255,255,255,0.97)"
        stroke="#d1d5db"
        strokeWidth={1}
        rx={6}
      />
      <text
        x={tx + 10}
        y={ty + 18}
        fontSize="12"
        fontWeight={600}
        fill="#0f172a"
      >
        {w.name ?? w.api10}
      </text>
      <text x={tx + 10} y={ty + 34} fontSize="11" fill="#374151">
        {w.formation_blueox ?? "—"}
        {w.formation ? ` (${w.formation})` : ""}
      </text>
      <text x={tx + 10} y={ty + 50} fontSize="11" fill="#374151">
        TVD {Math.round(point.tvd).toLocaleString()} ft
        {point.tvdEstimated ? " (estimated)" : ""}
      </text>
      <text x={tx + 10} y={ty + 66} fontSize="11" fill="#374151">
        Lateral{" "}
        {w.lateral_ft != null
          ? `${Math.round(w.lateral_ft).toLocaleString()} ft`
          : "—"}
      </text>
      <text x={tx + 10} y={ty + 82} fontSize="11" fill="#374151">
        First prod {w.first_prod_date ?? "—"}
      </text>
    </g>
  );
}

// ---------------- tick math ----------------

function niceTicks(min: number, max: number, count: number): number[] {
  if (min === max) return [min];
  const step = niceStep((max - min) / count);
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + 1e-9; v += step) out.push(round(v));
  return out;
}

function niceStep(rough: number): number {
  if (rough <= 0) return 1;
  const exp = Math.floor(Math.log10(rough));
  const frac = rough / Math.pow(10, exp);
  let mult: number;
  if (frac < 1.5) mult = 1;
  else if (frac < 3) mult = 2;
  else if (frac < 7) mult = 5;
  else mult = 10;
  return mult * Math.pow(10, exp);
}

function round(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}
