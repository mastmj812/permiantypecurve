import { describe, expect, it } from "vitest";

import { plusOffsetBearingDeg } from "./narvi";

// THE golden table (lateral azimuth -> compass bearing of +offset), gunbarrel
// sign rule v2 — byte-identical to backend tests/test_blueox_export.py and the
// narvi / erebor / engineering_db copies. Change every copy or none.
const GOLDEN_PLUS_BEARING: Array<[number, number]> = [
  [0.0, 90.0], [0.3, 90.3], [40.2, 130.2], [45.0, 135.0], [45.04, 135.04],
  [45.06, 315.06], [45.1, 315.1], [55.3, 325.3], [71.3, 341.3], [89.0, 359.0],
  [90.0, 0.0], [128.8, 38.8], [161.3, 71.3], [179.5, 89.5], [179.96, 89.96],
  [180.0, 90.0], [200.0, 110.0], [-18.7, 71.3],
];

describe("plusOffsetBearingDeg", () => {
  it("matches the golden table", () => {
    for (const [az, want] of GOLDEN_PLUS_BEARING) {
      const got = plusOffsetBearingDeg(az);
      const d = ((got - want + 540) % 360) - 180;
      expect(Math.abs(d), `az ${az}`).toBeLessThan(1e-6);
    }
  });
});
