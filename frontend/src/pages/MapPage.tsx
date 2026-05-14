import { MapView } from "../components/MapView";
import { FilterPanel } from "../panels/FilterPanel";
import { Legend } from "../panels/Legend";
import { MapToolbar } from "../panels/MapToolbar";
import { SummaryDrawer } from "../panels/SummaryDrawer";

export function MapPage() {
  return (
    <div className="page page-three-col">
      <FilterPanel />
      <div className="map-stage">
        <MapToolbar />
        <MapView />
        <Legend />
      </div>
      <SummaryDrawer />
    </div>
  );
}
