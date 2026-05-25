// Capture the slide preview's chart panels as separate PNGs, one
// per stream slide in the PPTX export plus a single shared map.
//
// The PowerPoint export's 4-slide structure is:
//   Slide 1 — Oil   (oil rate, oil cum, map)
//   Slide 2 — Gas   (gas rate, gas cum, same map)
//   Slide 3 — Water (water rate, water cum, same map)
//   Slide 4 — Type Curve Wells
//
// We send 3 × {rate, cum} panel PNGs plus 1 shared map PNG. Each
// panel is captured at the SVG/img's own getBoundingClientRect so
// the panel's CSS padding isn't baked into the image — that gutter
// would otherwise show up as visible whitespace inside the picture
// box on the slide.

import type { Stream } from "../../api/forecasts";

export interface StreamPanelImages {
  rate: Blob;
  cum: Blob;
  // Probit is captured only when the slide preview was rendered with
  // includeProbit (the iframe URL carried ?probit=1). When absent the
  // backend export defaults to the no-probit layout.
  probit?: Blob;
}

export interface PanelImages {
  oil: StreamPanelImages;
  gas: StreamPanelImages;
  water: StreamPanelImages;
  map: Blob;
}

const STREAMS: Stream[] = ["oil", "gas", "water"];

export async function captureSlidePanels(
  iframeDoc: Document,
  scale = 2,
): Promise<PanelImages> {
  const oil = await captureStream(iframeDoc, "oil", scale);
  const gas = await captureStream(iframeDoc, "gas", scale);
  const water = await captureStream(iframeDoc, "water", scale);

  // The map lives in only the oil section; we use the same snapshot
  // on all three stream slides since the cohort well positions don't
  // change per stream.
  const mapPanel = iframeDoc.querySelector<HTMLElement>("[data-slide-panel-map]");
  if (!mapPanel) {
    throw new Error("map panel not found");
  }
  const map = await capturePanel(iframeDoc, mapPanel, scale);

  return { oil, gas, water, map };
}

// Backwards-compat alias.
export { captureSlidePanels as captureSlideComposite };

async function captureStream(
  iframeDoc: Document,
  stream: Stream,
  scale: number,
): Promise<StreamPanelImages> {
  const ratePanel = iframeDoc.querySelector<HTMLElement>(
    `[data-slide-panel-rate][data-stream="${stream}"]`,
  );
  const cumPanel = iframeDoc.querySelector<HTMLElement>(
    `[data-slide-panel-cum][data-stream="${stream}"]`,
  );
  if (!ratePanel || !cumPanel) {
    throw new Error(`stream "${stream}" panels missing in iframe DOM`);
  }
  const rate = await capturePanel(iframeDoc, ratePanel, scale);
  const cum = await capturePanel(iframeDoc, cumPanel, scale);
  // Probit is optional — only rendered when the slide URL carried
  // ?probit=1. Capture if present, omit silently otherwise.
  const probitPanel = iframeDoc.querySelector<HTMLElement>(
    `[data-slide-panel-probit][data-stream="${stream}"]`,
  );
  const probit = probitPanel
    ? await capturePanel(iframeDoc, probitPanel, scale)
    : undefined;
  return { rate, cum, probit };
}

async function capturePanel(
  iframeDoc: Document,
  panel: HTMLElement,
  scale: number,
): Promise<Blob> {
  // Target the SVG or IMG INSIDE the panel, not the panel itself —
  // the `.slide-panel` wrapper has 4px CSS padding on every side, and
  // including that in the capture bakes a white gutter into the PNG
  // that ends up as visible whitespace inside the PPT picture box.
  const svg = panel.querySelector("svg");
  const img = panel.querySelector<HTMLImageElement>("img");
  const target: SVGElement | HTMLImageElement | null = svg ?? img;
  if (!target) {
    throw new Error("panel contains neither SVG nor IMG");
  }

  const rect = target.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) {
    throw new Error(`capture target has zero size`);
  }
  const canvas = iframeDoc.defaultView!.document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("could not get 2d context");

  if (svg) {
    canvas.width = Math.round(rect.width * scale);
    canvas.height = Math.round(rect.height * scale);
    ctx.scale(scale, scale);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, rect.width, rect.height);
    const rendered = await svgToImage(svg, rect.width * scale, rect.height * scale);
    ctx.drawImage(rendered, 0, 0, rect.width, rect.height);
  } else if (img) {
    // Map panel — img.src is a data URL of the MapLibre snapshot.
    // Use the SOURCE PNG's natural dimensions (the MapLibre canvas's
    // actual size) instead of the IMG element's CSS rect. Otherwise
    // if MapLibre was initialized at a different aspect than what the
    // IMG is currently sized at (e.g., after a hot prop change that
    // didn't trigger a fresh MapLibre init), drawing into a mismatched
    // rect stretches the map content. Native-size capture preserves
    // the map's aspect cleanly; PowerPoint can then size the picture
    // box to whatever target dimensions it likes — if both aspects
    // match (as they should when the iframe is keyed properly), no
    // stretching either way.
    const mapImg = await loadImage(img.src);
    const srcW = mapImg.naturalWidth || Math.round(rect.width * scale);
    const srcH = mapImg.naturalHeight || Math.round(rect.height * scale);
    canvas.width = srcW;
    canvas.height = srcH;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, srcW, srcH);
    ctx.drawImage(mapImg, 0, 0);
  }

  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error("canvas.toBlob returned null"))),
      "image/png",
    );
  });
}

async function svgToImage(
  svg: SVGElement,
  pxWidth: number,
  pxHeight: number,
): Promise<HTMLImageElement> {
  const clone = svg.cloneNode(true) as SVGElement;
  if (!clone.getAttribute("xmlns")) {
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  }
  // TypeCurveChart's SVGs declare explicit width/height but NO viewBox
  // — bumping the clone to a higher size would leave content sitting
  // at the original-coordinate top-left. Inject a viewBox so the
  // drawing scales to fill the new raster dimensions.
  if (!clone.getAttribute("viewBox")) {
    const origW = parseFloat(svg.getAttribute("width") || "0");
    const origH = parseFloat(svg.getAttribute("height") || "0");
    if (origW > 0 && origH > 0) {
      clone.setAttribute("viewBox", `0 0 ${origW} ${origH}`);
    }
  }
  clone.setAttribute("width", String(Math.round(pxWidth)));
  clone.setAttribute("height", String(Math.round(pxHeight)));
  const xml = new XMLSerializer().serializeToString(clone);
  const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    return await loadImage(url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`image load failed: ${src.slice(0, 64)}…`));
    img.src = src;
  });
}

// Used by tests / type-checking — silences the otherwise-unused
// STREAMS array warning that's actually used implicitly through the
// per-stream loop above.
void STREAMS;
