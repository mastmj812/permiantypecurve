// Per-shapefile visibility filter for the acreage-polygon (uploaded
// shapefile) layers. Shared by the Map tab, the Review map, and the
// slide-export map so all three honor the same show/hide toggles the
// user sets on the Map tab (`mapStore.dealVisibility`).

import type { FilterSpecification } from "maplibre-gl";

import { NO_SOURCE_FILE } from "../api/dealPolygons";

/**
 * Build the MapLibre `filter` for the acreage-polygon layers from the
 * per-shapefile visibility map.
 *
 * `dealVisibility` maps a shapefile's `source_file` name → whether it is
 * shown; a missing key (or `true`) means visible. Features whose
 * `source_file` is toggled off are hidden. Legacy rows with a null
 * `source_file` fall into the `NO_SOURCE_FILE` bucket via `coalesce` so
 * they can be toggled too. Returns a pass-through (`true`) filter when
 * nothing is hidden — the common case (default `{}` = all visible).
 */
export function dealVisibilityFilter(
  dealVisibility: Record<string, boolean>,
): FilterSpecification {
  const hidden = Object.entries(dealVisibility)
    .filter(([, visible]) => !visible)
    .map(([sourceFile]) => sourceFile);
  if (hidden.length === 0) {
    return ["literal", true] as unknown as FilterSpecification;
  }
  return [
    "!",
    [
      "in",
      ["coalesce", ["get", "source_file"], NO_SOURCE_FILE],
      ["literal", hidden],
    ],
  ] as unknown as FilterSpecification;
}
