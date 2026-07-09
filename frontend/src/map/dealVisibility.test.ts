import { describe, expect, it } from "vitest";

import { NO_SOURCE_FILE } from "../api/dealPolygons";
import { dealVisibilityFilter } from "./dealVisibility";

describe("dealVisibilityFilter", () => {
  it("passes everything through when nothing is toggled off", () => {
    expect(dealVisibilityFilter({}) as unknown).toEqual(["literal", true]);
    expect(
      dealVisibilityFilter({ "a.zip": true, "b.zip": true }) as unknown,
    ).toEqual(["literal", true]);
  });

  it("hides only the shapefiles toggled off", () => {
    const filter = dealVisibilityFilter({
      "a.zip": true,
      "b.zip": false,
    }) as unknown;
    expect(filter).toEqual([
      "!",
      [
        "in",
        ["coalesce", ["get", "source_file"], NO_SOURCE_FILE],
        ["literal", ["b.zip"]],
      ],
    ]);
  });

  it("hides multiple toggled-off shapefiles", () => {
    const filter = dealVisibilityFilter({
      "a.zip": false,
      "b.zip": true,
      "c.zip": false,
    }) as unknown;
    expect(filter).toEqual([
      "!",
      [
        "in",
        ["coalesce", ["get", "source_file"], NO_SOURCE_FILE],
        ["literal", ["a.zip", "c.zip"]],
      ],
    ]);
  });

  it("buckets a toggled-off null-source_file group under NO_SOURCE_FILE", () => {
    const filter = dealVisibilityFilter({ [NO_SOURCE_FILE]: false }) as unknown;
    expect(filter).toEqual([
      "!",
      [
        "in",
        ["coalesce", ["get", "source_file"], NO_SOURCE_FILE],
        ["literal", [NO_SOURCE_FILE]],
      ],
    ]);
  });
});
