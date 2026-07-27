// Amber pill shown wherever a risked curve's numbers are displayed —
// the visual contract that what you're reading carries the geologic
// multipliers (charts, EUR table, slide param table, exports).

import type { RiskMultipliers } from "../api/typeCurves";
import { isRisked, riskingLabel } from "./risking";

export function RiskingBadge({
  muls,
}: {
  muls: RiskMultipliers | null | undefined;
}) {
  if (!isRisked(muls)) return null;
  return (
    <span
      title={
        "Geologic risking applied — charts, EURs, and every export carry " +
        "these multipliers. The stored technical fit is unchanged underneath " +
        "(the tweak panel edits pre-risking values)."
      }
      style={{
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: 10,
        background: "#fef3c7",
        color: "#b45309",
        fontSize: 11,
        fontWeight: 600,
        whiteSpace: "nowrap",
      }}
    >
      risked {riskingLabel(muls)}
    </span>
  );
}
