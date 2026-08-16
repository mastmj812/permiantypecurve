// ReasonDialog — small modal capturing a coded reason + note for a
// cohort removal during map curation. The reason rides the cohort's
// manual_remove event and surfaces on the build-up sheet's not_selected
// stage — unless a filter stage claims the well first (stage order is
// first-stage-that-removes, so e.g. an off-filter drop attributes to
// `spacing` while the code stays in the event record and resurfaces if
// the filters are later relaxed). A parent-well cull thus reads
// "Parent-child / spacing" instead of an anonymous "not carried into
// cohort". One code applies to the whole batch — nuance goes in the note.
//
// Same modal-backdrop + ExclusionReasonControl pattern as the TC
// workspace's post-save remove dialog (TypeCurveWellsPage).

import { useState } from "react";

import type { ExclusionEntry, ReasonCode } from "../api/types";
import { ExclusionReasonControl } from "./ExclusionReasonControl";

export function ReasonDialog({
  title,
  detail,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  // One-line context under the title (e.g. what the reason lands on).
  detail: string;
  confirmLabel: string;
  onConfirm: (reason: ExclusionEntry) => void;
  onCancel: () => void;
}) {
  const [code, setCode] = useState<ReasonCode>("parent_child_spacing");
  const [note, setNote] = useState("");

  return (
    // zIndex 120: must stack above the floating inspect modal (its wrap
    // is z-index 100) and the buildup drawer (z-index 30) regardless of
    // which surface opened this dialog.
    <div className="modal-backdrop" style={{ zIndex: 120 }} onClick={onCancel}>
      <div
        className="modal"
        style={{ maxWidth: 420 }}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <strong>{title}</strong>
          <button type="button" className="link-btn" onClick={onCancel}>
            close
          </button>
        </header>
        <div className="modal-body" style={{ display: "grid", gap: 12 }}>
          <span className="muted" style={{ fontSize: 12 }}>
            {detail}
          </span>
          <ExclusionReasonControl
            code={code}
            note={note}
            autoFocusNote
            onChange={(c, n) => {
              setCode(c);
              setNote(n);
            }}
          />
          <div className="param-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={() => onConfirm({ code, note })}
            >
              {confirmLabel}
            </button>
            <button type="button" className="tb-btn" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
