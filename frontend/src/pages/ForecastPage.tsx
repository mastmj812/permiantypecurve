import { useCallback, useEffect, useState } from "react";

import {
  type ForecastRow,
  type WellCurvesResponse,
  batchForecast,
  fetchSyncStatus,
  fetchWellCurves,
  listForecasts,
} from "../api/forecasts";
import { ForecastDetailModal } from "../forecasts/ForecastDetailModal";
import { ForecastGrid } from "../forecasts/ForecastGrid";
import { useMapStore } from "../store/mapStore";

type FitStatus = "idle" | "running" | "succeeded" | "failed";

export function ForecastPage() {
  const api14s = useMapStore((s) => s.forecastApi14s);
  const setCurrentPage = useMapStore((s) => s.setCurrentPage);

  const [forecasts, setForecasts] = useState<ForecastRow[]>([]);
  const [curves, setCurves] = useState<Record<string, WellCurvesResponse | undefined>>({});
  const [status, setStatus] = useState<FitStatus>("idle");
  const [statusNote, setStatusNote] = useState<string>("");
  const [openApi14, setOpenApi14] = useState<string | null>(null);

  // Kick off the batch fit once when the page loads with a fresh selection.
  useEffect(() => {
    if (api14s.length === 0) return;
    let cancelled = false;

    (async () => {
      setStatus("running");
      setStatusNote(`fitting ${api14s.length} wells…`);
      try {
        const b = await batchForecast(api14s);
        setStatusNote(`job ${b.job_id.slice(0, 8)} — polling…`);
        // Poll the sync_jobs row until status flips.
        for (let i = 0; i < 600; i++) {
          if (cancelled) return;
          await new Promise((r) => setTimeout(r, 1000));
          const s = await fetchSyncStatus();
          const job = s.recent_jobs.find((j) => j.id === b.job_id);
          if (!job) continue;
          if (job.status === "succeeded") {
            setStatus("succeeded");
            setStatusNote(`fit complete — ${job.items_upserted} of ${api14s.length} wells`);
            break;
          }
          if (job.status === "failed") {
            setStatus("failed");
            setStatusNote(job.error ?? "fit failed");
            return;
          }
        }
        await refresh();
      } catch (e) {
        if (cancelled) return;
        setStatus("failed");
        setStatusNote(String(e));
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api14s.join(",")]);

  const refresh = useCallback(async () => {
    if (api14s.length === 0) return;
    const rows = await listForecasts(api14s);
    setForecasts(rows);
    // Pull curves in parallel for sparkline rendering.
    const newCurves: Record<string, WellCurvesResponse> = {};
    await Promise.all(
      api14s.map(async (a) => {
        try {
          newCurves[a] = await fetchWellCurves(a);
        } catch {
          /* well had no forecast / curves — leave undefined */
        }
      }),
    );
    setCurves(newCurves);
  }, [api14s]);

  // If the user navigates straight to the page without carrying a fresh
  // selection over, fall back to the current map selection so the page
  // isn't blank.
  const mapSelection = useMapStore((s) => s.selectedApi14s);
  useEffect(() => {
    if (api14s.length === 0 && mapSelection.size > 0) {
      useMapStore.getState().setForecastApi14s(Array.from(mapSelection));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="forecast-page">
      <header className="forecast-page-header">
        <span>
          Forecasts {api14s.length > 0 && <span className="muted">({api14s.length} wells)</span>}
        </span>
        <div className="toolbar-group">
          <span className={`status-pill status-${status}`}>{status}</span>
          {statusNote && <span className="muted">{statusNote}</span>}
          <button type="button" className="tb-btn" onClick={() => refresh()}>
            refresh
          </button>
          <button type="button" className="tb-btn" onClick={() => setCurrentPage("map")}>
            ← back to map
          </button>
        </div>
      </header>

      <ForecastGrid
        forecasts={forecasts}
        curvesByApi14={curves}
        onRowClick={(api14) => setOpenApi14(api14)}
      />

      {openApi14 && (
        <ForecastDetailModal
          api14={openApi14}
          forecasts={forecasts.filter((f) => f.api14 === openApi14)}
          onClose={() => setOpenApi14(null)}
          onSaved={(updated) => {
            setForecasts((prev) =>
              prev.map((f) => (f.id === updated.id ? updated : f)),
            );
          }}
        />
      )}
    </div>
  );
}
