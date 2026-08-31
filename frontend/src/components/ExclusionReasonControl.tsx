// Compact reason-code picker + note input for well exclusions — the
// user-authored half of the type-well build-up. Used inline in the
// Review table's flags cell (excluded rows) and inside the TC
// workspace's remove-well dialog. Presentation-only: the caller owns
// where the entry lives (mapStore exclusions vs. a PATCH body).

import { REVIEW_REASON_CODES, type ReasonCode } from "../api/types";

export function ExclusionReasonControl({
  code,
  note,
  onChange,
  autoFocusNote = false,
}: {
  code: ReasonCode;
  note: string;
  onChange: (code: ReasonCode, note: string) => void;
  autoFocusNote?: boolean;
}) {
  return (
    <span
      className="exclusion-reason-control"
      style={{ display: "inline-flex", gap: 4, alignItems: "center" }}
      // The Review table row opens the detail modal on click — edits
      // to the reason must not bubble into that.
      onClick={(e) => e.stopPropagation()}
    >
      <select
        value={code}
        title="Exclusion reason (grouped on the build-up sheet)"
        onChange={(e) => onChange(e.target.value as ReasonCode, note)}
        style={{ fontSize: 13 }}
      >
        {Object.entries(REVIEW_REASON_CODES).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <input
        type="text"
        value={note}
        placeholder="note (optional)"
        title="Free-text nuance, e.g. 'frac hit from Smith 2H in month 4'"
        autoFocus={autoFocusNote}
        maxLength={500}
        onChange={(e) => onChange(code, e.target.value)}
        style={{ fontSize: 13, width: 150 }}
      />
    </span>
  );
}
