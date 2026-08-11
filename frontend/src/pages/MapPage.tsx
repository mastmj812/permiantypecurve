import { useEffect, useState } from "react";

import { BuildupDrawer } from "../components/BuildupDrawer";
import { CohortBar } from "../components/CohortBar";
import { InspectModal } from "../components/InspectModal";
import { MapView } from "../components/MapView";
import { FilterPanel } from "../panels/FilterPanel";
import { Legend } from "../panels/Legend";
import { MapToolbar } from "../panels/MapToolbar";
import { SummaryDrawer } from "../panels/SummaryDrawer";

export function MapPage() {
  // Inspect modal is owned here so the cohort bar stays focused on
  // its own state; the bar dispatches `cohort:open-inspect` with the
  // staged api10s and we render the modal in response. The Buildup
  // drawer follows the same pattern (`cohort:open-buildup`, no
  // payload — it reads the active cohort + filters itself).
  const [inspectApi10s, setInspectApi10s] = useState<string[] | null>(null);
  const [showBuildup, setShowBuildup] = useState(false);

  useEffect(() => {
    function onOpen(e: Event) {
      const detail = (e as CustomEvent<{ api10s: string[] }>).detail;
      if (detail?.api10s?.length) setInspectApi10s(detail.api10s);
    }
    function onOpenBuildup() {
      setShowBuildup(true);
    }
    window.addEventListener("cohort:open-inspect", onOpen);
    window.addEventListener("cohort:open-buildup", onOpenBuildup);
    return () => {
      window.removeEventListener("cohort:open-inspect", onOpen);
      window.removeEventListener("cohort:open-buildup", onOpenBuildup);
    };
  }, []);

  return (
    <div className="page page-three-col">
      <FilterPanel />
      <div className="map-stage">
        <CohortBar />
        {/* Canvas wrapper gives MapView/Legend/Toolbar a sized,
            positioned ancestor that doesn't include the cohort bar —
            MapView's .map-root is position: absolute inset:0 and would
            otherwise paint over the bar. */}
        <div className="map-stage-canvas">
          <MapToolbar />
          <MapView />
          <Legend />
          {showBuildup && (
            <BuildupDrawer onClose={() => setShowBuildup(false)} />
          )}
        </div>
      </div>
      <SummaryDrawer />
      {inspectApi10s && (
        <InspectModal
          api10s={inspectApi10s}
          onClose={() => setInspectApi10s(null)}
        />
      )}
    </div>
  );
}
