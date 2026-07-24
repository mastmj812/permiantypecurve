// IntersectionObserver gate for heavyweight panels: true while the
// element is within `margin` of the viewport. Drives lazy map mounting
// on the dossier page — a deal can pin a dozen narvi scenarios, and a
// live MapLibre instance per panel (each a WebGL context created with
// preserveDrawingBuffer) exhausts the browser's ~16-context-per-page
// cap (maps go blank) and OOMs the tab. When `enabled` is false the
// hook reports true unconditionally (eager mount, the pre-dossier
// behavior the single-map slide pages keep).

import { type RefObject, useEffect, useState } from "react";

export function useNearViewport(
  ref: RefObject<Element | null>,
  enabled: boolean,
  margin = "600px",
): boolean {
  const [near, setNear] = useState(!enabled);
  useEffect(() => {
    if (!enabled) {
      setNear(true);
      return;
    }
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => setNear(entries.some((e) => e.isIntersecting)),
      { rootMargin: margin },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [ref, enabled, margin]);
  return near;
}
