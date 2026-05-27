"""TC-scoped forecast override resolution.

A Type Curve can carry per-well forecast overrides in
``type_curves.forecast_overrides``. When an override is present for a
given (api14, stream) pair, it wins over the global ``forecasts`` row.
This module is the single source of truth for that fallback rule so
Phase 1 reads and Phase 2 writes never diverge on what "the resolved
forecast for this well in this TC" means.

Shape contract for ``type_curves.forecast_overrides``::

    {
      "<api14>": {
        "oil":   { "params": {...}, "qi": ..., "Di": ..., "b": ...,
                   "Df": ..., "eur": ..., "model_type": "...",
                   "fit_r2": ..., "manual_override": true },
        "gas":   { ... },
        "water": { ... },
      },
      ...
    }

Keys are optional — a TC may override only oil, only one well, etc.
Missing entries fall through to the global ``Forecast`` row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.db.models import Forecast, TypeCurve
from app.db.models.forecasts import Stream

Source = Literal["override", "global", "missing"]


@dataclass(frozen=True)
class ResolvedForecast:
    """One stream's resolved forecast for a TC + well.

    ``source`` discriminates where the payload came from so the UI can
    surface badges ("edited for this TC") without a second round-trip.
    ``payload`` matches the override JSONB shape — the same per-stream
    bundle the frontend reads from the global forecast row.
    """

    api14: str
    stream: Stream
    source: Source
    payload: dict[str, Any] | None  # None when source == "missing"


def _payload_from_global(f: Forecast) -> dict[str, Any]:
    """Project a global Forecast row into the override-payload shape so
    callers don't have to special-case override vs global downstream."""
    return {
        "id": str(f.id),
        "params": dict(f.params or {}),
        "model_type": f.model_type.value,
        "qi": f.qi,
        "di_initial": f.di_initial,
        "b": f.b,
        "df_terminal": f.df_terminal,
        "eur": f.eur,
        "peak_rate": f.peak_rate,
        "peak_month_date": f.peak_month_date.isoformat() if f.peak_month_date else None,
        "fit_method": f.fit_method.value,
        "fit_r2": f.fit_r2,
        "fit_rmse": f.fit_rmse,
        "downtime_ratio": f.downtime_ratio,
        "manual_override": f.manual_override,
        "locked": f.locked,
    }


def resolve_forecast(
    tc: TypeCurve,
    api14: str,
    stream: Stream,
    global_forecast: Forecast | None,
) -> ResolvedForecast:
    """Return the override → global → missing fallback for one (well, stream).

    Phase 2 will reuse this for write-back: an edit in TC-context creates
    a new entry under ``tc.forecast_overrides[api14][stream]``; an edit
    in Review-context patches ``global_forecast``. Either way this is
    where the read of "what should the workspace show" lives.
    """
    overrides = tc.forecast_overrides or {}
    well_overrides = overrides.get(api14) or {}
    override_payload = well_overrides.get(stream.value)
    if override_payload:
        return ResolvedForecast(
            api14=api14, stream=stream, source="override", payload=dict(override_payload)
        )
    if global_forecast is not None:
        return ResolvedForecast(
            api14=api14,
            stream=stream,
            source="global",
            payload=_payload_from_global(global_forecast),
        )
    return ResolvedForecast(api14=api14, stream=stream, source="missing", payload=None)
