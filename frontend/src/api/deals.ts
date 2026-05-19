// Deal API client. Mirrors app/api/deals.py.
//
// A deal groups type curves so the engineer can export the whole package
// as one Excel workbook (per curve: a metadata sheet + a fitted-forecast
// sheet for oil/gas/water, out to 50 years).

import { apiFetch } from "./auth";
import type { TypeCurveSummary } from "./typeCurves";

export interface DealSummary {
  id: string;
  name: string;
  notes: string | null;
  created_at: string;
  n_curves: number;
}

export interface DealRow {
  id: string;
  name: string;
  notes: string | null;
  created_at: string;
  curves: TypeCurveSummary[];
}

export async function listDeals(): Promise<DealSummary[]> {
  const r = await apiFetch("/api/deals");
  if (!r.ok) throw new Error(`list deals failed: ${r.status}`);
  return (await r.json()) as DealSummary[];
}

export async function getDeal(id: string): Promise<DealRow> {
  const r = await apiFetch(`/api/deals/${id}`);
  if (!r.ok) throw new Error(`fetch deal failed: ${r.status}`);
  return (await r.json()) as DealRow;
}

export async function createDeal(args: {
  name: string;
  notes?: string | null;
}): Promise<DealRow> {
  const r = await apiFetch("/api/deals", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name: args.name, notes: args.notes ?? null }),
  });
  if (!r.ok) {
    // 409 on duplicate name — surface the server's detail so the user
    // sees "deal name already exists" rather than a bare status code.
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`create deal failed: ${detail}`);
  }
  return (await r.json()) as DealRow;
}

export async function patchDeal(
  id: string,
  body: { name?: string; notes?: string | null },
): Promise<DealRow> {
  const r = await apiFetch(`/api/deals/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`patch deal failed: ${detail}`);
  }
  return (await r.json()) as DealRow;
}

export async function deleteDeal(id: string): Promise<void> {
  const r = await apiFetch(`/api/deals/${id}`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) {
    throw new Error(`delete deal failed: ${r.status}`);
  }
}

// xlsx export — same blob-download trick as downloadTypeCurveExport,
// because <a download> can't attach the bearer token.
export async function downloadDealExport(
  id: string,
  filename = "deal.xlsx",
): Promise<void> {
  const r = await apiFetch(`/api/deals/${id}/export.xlsx`);
  if (!r.ok) {
    const detail = (await safeDetail(r)) ?? `${r.status}`;
    throw new Error(`export deal failed: ${detail}`);
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function safeDetail(r: Response): Promise<string | null> {
  try {
    const body = (await r.json()) as { detail?: string };
    return body.detail ?? null;
  } catch {
    return null;
  }
}
