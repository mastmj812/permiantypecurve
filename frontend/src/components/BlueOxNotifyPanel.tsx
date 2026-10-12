// "Notify Blue Ox" — posts the drop notice to Blue Ox's #eng-updates
// (Steven's only notification; there is no email). Targets the deal's
// LATEST logged export. Flow: export → file the .xlsx in the Deal
// Folder → tick "filed" → post. The draft (supersedes + zone/count
// diff) is generated server-side and stays editable; the codename is
// the ArcMap / Deal Folder name (e.g. "RALLY CAPS"), not the lowercase
// config codename in the filename.

import { useEffect, useState } from "react";

import { type BlueOxNotifyDraft, getBlueOxNotifyDraft, postBlueOxNotify } from "../api/deals";

interface Props {
  dealId: string;
  // Bumped by the modal after each export so the draft reloads.
  refreshKey: number;
}

export function BlueOxNotifyPanel({ dealId, refreshKey }: Props) {
  const [draft, setDraft] = useState<BlueOxNotifyDraft | null>(null);
  const [codename, setCodename] = useState("");
  const [body, setBody] = useState("");
  const [filed, setFiled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [posted, setPosted] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const d = await getBlueOxNotifyDraft(dealId);
        if (!alive) return;
        setDraft(d);
        setCodename(d.codename ?? "");
        setBody(d.body ?? "");
        setFiled(false);
        setError(null);
        setPosted(null);
      } catch (e) {
        if (alive) setError(String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, [dealId, refreshKey]);

  if (!draft) {
    return error ? <div className="alert alert-error">{error}</div> : <p className="muted">loading…</p>;
  }

  const preview = `${codename.trim() || "<CODENAME>"} ${body.trim()}`;
  const blocked = draft.blocked ?? (draft.configured ? null : "SLACK_ENG_UPDATES_WEBHOOK_URL is not set on the backend");
  const canPost = !blocked && !posted && !busy && filed && codename.trim() !== "" && body.trim() !== "";

  const onPost = async () => {
    if (!draft.export_id) return;
    if (!window.confirm(`Post to #eng-updates?\n\n${preview}`)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await postBlueOxNotify(dealId, {
        export_id: draft.export_id,
        codename: codename.trim(),
        body: body.trim(),
        filed_in_deal_folder: filed,
      });
      setPosted(r.text);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      {draft.filename && (
        <p style={{ margin: "0 0 6px" }}>
          latest export <b>{draft.filename}</b>
          {draft.exported_at && <span className="muted"> · {new Date(draft.exported_at).toLocaleString()}</span>}
          {draft.supersedes && <span> · supersedes <b>{draft.supersedes}</b></span>}
        </p>
      )}
      {blocked && <div className="alert" style={{ marginBottom: 6 }}>{blocked}</div>}
      {error && <div className="alert alert-error" style={{ marginBottom: 6 }}>{error}</div>}
      {posted ? (
        <div className="alert" style={{ marginBottom: 6 }}>posted to #eng-updates: {posted}</div>
      ) : (
        draft.export_id && (
          <>
            <label style={{ display: "block", marginBottom: 6 }}>
              Blue Ox codename{" "}
              <input value={codename} size={24} placeholder="Deal Folder name, e.g. RALLY CAPS"
                onChange={(e) => setCodename(e.target.value)} />
            </label>
            <textarea value={body} rows={4} style={{ width: "100%", fontFamily: "inherit" }}
              onChange={(e) => setBody(e.target.value)} />
            <p className="muted" style={{ margin: "4px 0", fontSize: 12 }}>
              Edit freely: name zone renames, and state the risk basis (geologic vs
              development/spacing) — the manifest token always reads geologic_multipliers_applied.
            </p>
            <label style={{ display: "block", margin: "6px 0" }}>
              <input type="checkbox" checked={filed} onChange={(e) => setFiled(e.target.checked)} />
              {" "}{draft.filename} is filed in the Deal Folder (the post is Steven&apos;s signal it is there)
            </label>
            <button type="button" className="tb-btn" disabled={!canPost} onClick={() => void onPost()}>
              {busy ? "posting…" : "Post to #eng-updates"}
            </button>
          </>
        )
      )}
    </div>
  );
}
