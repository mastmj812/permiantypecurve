"""Run forecasts for one or many wells across oil/gas/water streams.

Glue between the math (`fit.py`) and the DB (`forecasts` table). Key
domain rule: EVERY stream anchors on its OWN detected peak. Water peaks
hard in month 0-1 (flowback), months before oil ramps up; gas commonly
peaks AFTER oil as the GOR climbs (39% of forecasted wells in this
dataset, p90 +4 months, with the true gas peak up to 1.5x the gas rate
at the oil month) — inheriting the oil peak under-read each stream's
qi and started the fit on the wrong limb. A stream with no real
production (no detectable peak / zero-rate peak) is SKIPPED — no
forecast row — rather than fit against zeros anchored on the oil
month, which under peak-anchored qi bounds manufactured phantom
oil-scale forecasts. Each stream's per-well forecast is expressed in
years-since-first-prod (the peak is an internal ramp anchor), so
per-stream peaks stay coherent — all three streams still map back to
one first-prod calendar. See ``detect_stream_peaks``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

import pandas as pd

# The per-well fitting pipeline lives in the shared ``boxfit`` package
# (BOX step 5a); this module is the DB wrapper around it. The names below
# are re-exported so existing ``app.forecasting.orchestrator`` imports keep
# working.
from boxfit.well import (
    _PEAK_RATE_COLUMN as _PEAK_RATE_COLUMN,
)
from boxfit.well import (
    _STREAM_RATE_COL as _STREAM_RATE_COL,
)
from boxfit.well import (
    MONTHLY_COLUMNS as MONTHLY_COLUMNS,
)
from boxfit.well import (
    STREAMS as STREAMS,
)
from boxfit.well import (
    _stream_rate_at_index as _stream_rate_at_index,
)
from boxfit.well import (
    detect_stream_peaks as detect_stream_peaks,
)
from boxfit.well import (
    df_terminal_for_cohort as df_terminal_for_cohort,
)
from boxfit.well import (
    df_terminal_for_subbasin as df_terminal_for_subbasin,
)
from boxfit.well import (
    fit_well_streams as fit_well_streams,
)
from boxfit.well import (
    prepare_monthly as prepare_monthly,
)
from boxfit.well import (
    stream_rate_at_first_prod as stream_rate_at_first_prod,
)
from boxfit.well import (
    stream_rate_at_peak as stream_rate_at_peak,
)
from sqlalchemy import null, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Forecast, ProductionMonthly, Stream, Well
from app.forecasting.types import ForecastConfig, ForecastResult

log = get_logger("forecasting.orchestrator")


def _load_monthly(session: Session, api10: str) -> pd.DataFrame:
    rows = session.execute(
        select(
            ProductionMonthly.prod_date,
            ProductionMonthly.oil_bbl,
            ProductionMonthly.gas_mcf,
            ProductionMonthly.water_bbl,
            ProductionMonthly.producing_days,
            ProductionMonthly.rate_calday_bopd,
            ProductionMonthly.rate_calday_mcfd,
            ProductionMonthly.rate_calday_bwpd,
        )
        .where(ProductionMonthly.api10 == api10)
        .order_by(ProductionMonthly.prod_date)
    ).all()
    df = pd.DataFrame(rows, columns=list(MONTHLY_COLUMNS))
    # NULL volumes / rates → 0 (one NaN poisons the cum-fit; see prepare_monthly).
    return prepare_monthly(df)


def _persist(
    session: Session,
    *,
    api10: str,
    stream: str,
    result: ForecastResult,
) -> uuid.UUID:
    """Upsert (api10, stream) → forecasts. Honors `locked` if a prior row
    exists — locked forecasts are skipped so a bulk re-fit doesn't wipe
    a user's manual override."""
    existing = session.execute(
        select(Forecast).where(Forecast.api10 == api10, Forecast.stream == Stream(stream))
    ).scalar_one_or_none()
    if existing is not None and existing.locked:
        log.info("forecast_locked_skip", api10=api10, stream=stream)
        return existing.id

    values = {
        "api10": api10,
        "stream": stream,
        "model_type": result.model_type,
        "params": result.params,
        "qi": result.qi,
        "di_initial": result.di_initial,
        "b": result.b,
        "df_terminal": result.df_terminal,
        "qo": result.qo,
        "peak_index_months": result.peak_index_months,
        "eur": result.eur,
        "peak_month_date": result.peak_month_date,
        "peak_rate": result.peak_rate,
        "fit_method": result.fit_method,
        "fit_r2": result.fit_r2,
        "fit_rmse": result.fit_rmse,
        "downtime_ratio": result.downtime_ratio,
        # Always written, so a refit clears a stale payload (e.g. a former
        # cohort_transfer row's donor block). SQL NULL, not JSON null.
        "diagnostics": result.diagnostics if result.diagnostics is not None else null(),
        "manual_override": False,
        "locked": False,
        "updated_at": datetime.now(UTC),
    }
    if existing is None:
        values["id"] = uuid.uuid4()
        values["created_at"] = datetime.now(UTC)

    stmt = pg_insert(Forecast.__table__).values(**values)
    # `locked` stays excluded from the update set (locked rows never
    # reach this point anyway — see the early return above). But
    # `manual_override` IS overwritten (to False, from `values`): when
    # the machine replaces an unlocked row's params, the row's
    # provenance must read "machine fit" — previously the stale True
    # survived the overwrite and the grid showed engineer provenance
    # on autofit values.
    update_cols = {
        c: stmt.excluded[c]
        for c in values
        if c not in {"id", "api10", "stream", "created_at", "locked"}
    }
    stmt = stmt.on_conflict_do_update(constraint="uq_forecasts_api10_stream", set_=update_cols)
    session.execute(stmt)
    session.commit()
    return values.get("id") or existing.id  # type: ignore[union-attr]


def _prune_stale_stream_forecast(session: Session, *, api10: str, stream: str) -> None:
    """Delete a leftover forecast row for a stream today's data can't fit.

    Fires from the no-peak skip branch: the well HAS production, but this
    stream's peak window is all-zero — usually a vendor restatement
    (Novi production-sharing re-allocation zeroing early water) that
    rewrote history under a stored fit. A skip writes nothing, so
    without this the stale row survives every refit and feeds the TC
    band with a fit whose data basis no longer exists (UL 20 unit,
    2026-08: three phantom ~2-3 MMbbl water EURs).

    Machine rows only: ``locked`` rows are never touched (same contract
    as ``_persist``), and ``manual_override`` rows are the engineer's
    work — surfaced by the manual-override guard, not silently deleted.
    """
    existing = session.execute(
        select(Forecast).where(Forecast.api10 == api10, Forecast.stream == Stream(stream))
    ).scalar_one_or_none()
    if existing is None:
        return
    if existing.locked or existing.manual_override:
        log.info(
            "stale_stream_forecast_kept",
            api10=api10,
            stream=stream,
            locked=existing.locked,
            manual_override=existing.manual_override,
        )
        return
    session.delete(existing)
    session.commit()
    log.warning("stale_stream_forecast_deleted", api10=api10, stream=stream)


def forecast_well(
    session: Session,
    api10: str,
    *,
    config: ForecastConfig | None = None,
    persist: bool = True,
) -> dict[str, ForecastResult | None]:
    """Fit all three streams for a single well.

    Every stream anchors on its OWN detected peak (see
    ``detect_stream_peaks``). Each stream's ramp prefix is anchored
    on its own ONSET (first producing month) rather than the well's
    first-prod, so leading zero / sub-floor months don't inflate the ramp
    length or the type-curve timing (``onset_index_months`` records the
    offset; see ``detect_onset``). Returns a dict {stream: ForecastResult
    or None on fit failure}. When `persist=True` (default), each
    successful result is written to the forecasts table via upsert.
    """
    well_row = session.execute(
        select(Well.subbasin, Well.formation_blueox).where(Well.api10 == api10)
    ).one_or_none()
    subbasin = well_row.subbasin if well_row is not None else None
    formation_blueox = well_row.formation_blueox if well_row is not None else None
    monthly = _load_monthly(session, api10)

    def _on_result(stream: str, result: ForecastResult) -> None:
        if persist:
            _persist(session, api10=api10, stream=stream, result=result)

    def _on_no_peak(stream: str) -> None:
        # Data can't support this stream anymore — prune any leftover
        # unlocked machine fit so it doesn't keep feeding the TC band.
        if persist:
            _prune_stale_stream_forecast(session, api10=api10, stream=stream)

    return fit_well_streams(
        monthly,
        config=config,
        subbasin=subbasin,
        formation_blueox=formation_blueox,
        api10=api10,
        on_result=_on_result,
        on_no_peak=_on_no_peak,
    )


class ManualOverrideGuardError(RuntimeError):
    """A bulk refit was asked to run over forecast rows in the ambiguous
    ``manual_override=True, locked=False`` state.

    Such a row carries an engineer's edit (``manual_override``) that was never
    locked. ``_persist`` only protects ``locked`` rows, so a blind bulk refit
    would silently overwrite the edited params AND reset ``manual_override`` to
    False — losing both the value and its provenance. The remedy is to triage
    first (lock the keepers, clear ``manual_override`` on the rest); this guard
    refuses the refit until that ambiguous set is empty so the loss can never
    recur.
    """

    def __init__(self, at_risk: list[tuple[str, str]]) -> None:
        self.at_risk = at_risk
        n = len(at_risk)
        sample = ", ".join(f"{a}/{s}" for a, s in at_risk[:5])
        more = "" if n <= 5 else f" (+{n - 5} more)"
        super().__init__(
            f"Refusing bulk refit: {n} forecast row(s) are manual_override=True "
            f"and locked=False and would be silently overwritten. Lock the "
            f"keepers and clear manual_override on the rest (triage), then "
            f"retry. At risk: {sample}{more}."
        )


def at_risk_forecasts(forecasts: Iterable[Forecast]) -> list[Forecast]:
    """Pure predicate for the rows a bulk refit would silently overwrite:
    ``manual_override=True`` and ``locked=False``.

    ``locked`` rows are already protected by ``_persist``; an unlocked manual
    edit is the ambiguous "bomb" state. Kept as a pure function (mirroring the
    SQL in ``find_at_risk_rows``) so the guard's rule is unit-testable without
    a database, matching this repo's DB-free test style."""
    return [f for f in forecasts if f.manual_override and not f.locked]


def find_at_risk_rows(
    session: Session, api10s: Iterable[str] | None = None
) -> list[tuple[str, str]]:
    """``(api10, stream)`` for every forecast in the ambiguous
    ``manual_override=True, locked=False`` state, optionally scoped to
    ``api10s``.

    Authoritative SQL mirror of ``at_risk_forecasts``, used by the bulk-refit
    guard and the dry-run. An empty list means a bulk refit is safe."""
    stmt = select(Forecast.api10, Forecast.stream).where(
        Forecast.manual_override.is_(True),
        Forecast.locked.is_(False),
    )
    if api10s is not None:
        stmt = stmt.where(Forecast.api10.in_(list(api10s)))
    out: list[tuple[str, str]] = []
    for row in session.execute(stmt).all():
        api10 = row[0]
        stream = row[1]
        out.append((api10, stream.value if isinstance(stream, Stream) else str(stream)))
    return out


def bulk_refit_dry_run(
    session: Session, api10s: Iterable[str] | None = None
) -> list[tuple[str, str]]:
    """Report the ``(api10, stream)`` rows a bulk refit would refuse to touch —
    the ambiguous ``manual_override=True, locked=False`` set — WITHOUT fitting
    or persisting anything. An empty list means a bulk refit is safe. This is
    the operational counterpart to the guard in ``forecast_wells``."""
    return find_at_risk_rows(session, api10s)


def forecast_wells(
    session: Session,
    api10s: Iterable[str],
    *,
    config: ForecastConfig | None = None,
) -> dict[str, dict[str, ForecastResult | None]]:
    """Bulk-forecast helper for the batch endpoint.

    Guard: refuses to run if any target row is in the ambiguous
    ``manual_override=True, locked=False`` state (raises
    ``ManualOverrideGuardError``). ``_persist`` protects ``locked`` rows but
    overwrites unlocked manual edits, so a blind bulk refit would silently
    wipe them — the guard forces those rows to be triaged (locked or cleared)
    first. ``reset_forecast_flags --refit`` clears the flags before calling
    this, so that sanctioned path passes cleanly; the batch API refit is the
    path this actually protects."""
    api10s = list(api10s)
    at_risk = find_at_risk_rows(session, api10s)
    if at_risk:
        raise ManualOverrideGuardError(at_risk)
    results: dict[str, dict[str, ForecastResult | None]] = {}
    for api10 in api10s:
        results[api10] = forecast_well(session, api10, config=config)
    return results
