import { useEffect, useState } from "react";

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
  // staged api10s and we render the modal in response.
  const [inspectApi10s, setInspectApi10s] = useState<string[] | null>(null);

  useEffect(() => {
    function onOpen(e: Event) {
      const detail = (e as CustomEvent<{ api10s: string[] }>).detail;
      if (detail?.api10s?.length) setInspectApi10s(detail.api10s);
    }
    window.addEventListener("cohort:open-inspect", onOpen);
    return () => window.removeEventListener("cohort:open-inspect", onOpen);
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
