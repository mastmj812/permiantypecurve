// Blue Ox Deliverable B (contract §2) — the PDP workbook, from the PDP tab.
//
// Inputs are Blue Ox kickoff items: the deal EFFECTIVE DATE (row 1 = that
// month if it's the 1st, else the next month) and the GROUPING (default
// one sheet per well: WI differs inside leases, and Blue Ox can always sum
// but never split). Preview shows each group's totals and the readiness
// warnings (unlocked streams, open review flags) — warnings never block;
// contract violations do. Michael sends the file; this stack never does.

import { useEffect, useState } from "react";

import {
  downloadPdpExport,
  getPdpConfig,
  previewPdpExport,
  putPdpExportConfig,
  type PdpExportPreview,
  type PdpGrouping,
} from "../api/pdp";

function kvol(v: number): string {
  return (v / 1000).toLocaleString(undefined, {
    maximumFractionDigits: 1,
    minimumFractionDigits: 1,
  });
}

export function PdpExportPanel({
  dealId,
  onClose,
}: {
  dealId: string;
  onClose: () => void;
}) {
  const [effective, setEffective] = useState("");
  const [grouping, setGrouping] = useState<PdpGrouping>("well");
  const [supersedes, setSupersedes] = useState("");
  const [preview, setPreview] = useState<PdpExportPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPdpConfig(dealId)
      .then((c) => {
        if (cancelled || !c?.export) return;
        setEffective(c.export.effective_date);
        setGrouping(
          c.export.grouping === "custom" ? "custom" : c.export.grouping,
        );
      })
      .catch((e: unknown) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, [dealId]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const saveAndPreview = () =>
    void act(async () => {
      await putPdpExportConfig(dealId, {
        effective_date: effective,
        grouping,
        supersedes: supersedes.trim() || null,
      });
      setPreview(await previewPdpExport(dealId));
    });

  const download = () =>
    void act(async () => {
      if (preview) await downloadPdpExport(dealId, preview.filename);
    });

  const totals = preview?.groups.reduce(
    (t, g) => ({
      oil: t.oil + g.eur_oil,
      gas: t.gas + g.eur_gas,
      water: t.water + g.eur_water,
    }),
    { oil: 0, gas: 0, water: 0 },
  );

  return (
    <div className="pdp-export">
      <div className="pdp-export-head">
        <strong>Blue Ox PDP workbook</strong>
        <span className="muted">
          contract §2 · curves mode · you send it, not the app
        </span>
        <button type="button" className="link-btn" onClick={onClose}>
          close
        </button>
      </div>
      <div className="pdp-export-form">
        <label>
          Effective date{" "}
          <input
            type="date"
            value={effective}
            onChange={(e) => setEffective(e.target.value)}
          />
        </label>
        <label>
          Grouping{" "}
          <select
            value={grouping}
            onChange={(e) => setGrouping(e.target.value as PdpGrouping)}
          >
            <option value="well">one sheet per well</option>
            <option value="lease">one sheet per lease</option>
            {grouping === "custom" && (
              <option value="custom">custom (saved)</option>
            )}
          </select>
        </label>
        <label title="Re-export only: the prior governing file this one replaces">
          Supersedes{" "}
          <input
            value={supersedes}
            placeholder="(first drop)"
            onChange={(e) => setSupersedes(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="tb-btn"
          disabled={busy || !effective}
          onClick={saveAndPreview}
        >
          Save + preview
        </button>
        <button
          type="button"
          className="tb-btn-primary"
          disabled={busy || !preview || !!preview.contract_errors}
          onClick={download}
          title={
            preview?.contract_errors
              ? "fix the contract violations first"
              : undefined
          }
        >
          Download {preview?.filename ?? ".xlsx"}
        </button>
        {busy && <span className="status-pill status-running">working…</span>}
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {preview && (
        <>
          <p className="muted pdp-note">
            Row 1 = {preview.first_row_month.slice(0, 7)} ·{" "}
            {preview.curve_months} months · seller actuals through{" "}
            {preview.production_history_through}, forecast after ·{" "}
            {preview.groups.length} group(s)
            {totals && (
              <>
                {" "}
                · oil {kvol(totals.oil)} Mbbl · gas {kvol(totals.gas)} MMcf ·
                water {kvol(totals.water)} Mbbl
              </>
            )}
          </p>
          {preview.contract_errors && (
            <pre className="alert alert-error pdp-pre">
              {preview.contract_errors}
            </pre>
          )}
          {preview.findings.length > 0 && (
            <details>
              <summary>
                {preview.findings.length} readiness warning(s) — review before
                sending (not blocking)
              </summary>
              <ul className="pdp-breaks">
                {preview.findings.map((f) => (
                  <li key={`${f.check}${f.detail}`}>
                    <span className="badge badge-warn">
                      {f.check.replace("_", " ")}
                    </span>{" "}
                    {f.detail}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <details>
            <summary>Group totals</summary>
            <table className="pdp-table">
              <thead>
                <tr>
                  <th>Group (sheet)</th>
                  <th>Wells</th>
                  <th>Oil Mbbl</th>
                  <th>Gas MMcf</th>
                  <th>Water Mbbl</th>
                </tr>
              </thead>
              <tbody>
                {preview.groups.map((g) => (
                  <tr key={g.name}>
                    <td>{g.name}</td>
                    <td>{g.well_count}</td>
                    <td>{kvol(g.eur_oil)}</td>
                    <td>{kvol(g.eur_gas)}</td>
                    <td>{kvol(g.eur_water)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </div>
  );
}
