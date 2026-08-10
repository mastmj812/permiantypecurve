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
from dataclasses import replace
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Forecast, ProductionMonthly, Stream, Well
from app.forecasting.fit import (
    STREAM_DOWNTIME_FLOOR_FIELD,
    STREAM_ECON_LIMIT_FIELD,
    fit_rate_cum,
    fit_rate_time,
    fit_with_fallback,
)
from app.forecasting.peak_detection import (
    PeakResult,
    detect_onset,
    detect_peak,
)
from app.forecasting.ramp_arps import compute_total_eur
from app.forecasting.types import ForecastConfig, ForecastResult

log = get_logger("forecasting.orchestrator")

STREAMS: tuple[str, ...] = ("oil", "gas", "water")

# Rate column each stream's peak is detected on — every stream anchors
# on its OWN peak (gas previously inherited oil's; see module docstring
# for why that under-read gas on rising-GOR wells).
_PEAK_RATE_COLUMN: dict[str, str] = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_mcfd",
    "water": "rate_calday_bwpd",
}


def df_terminal_for_subbasin(subbasin: str | None, config: ForecastConfig) -> float:
    """Terminal Df for the well's Permian sub-basin. Midland uses the
    shallower ``df_terminal_midland`` (its boundary-dominated tails flatten
    more); Delaware and every other sub-basin use ``df_terminal_per_year``
    (the policy: Midland 0.06, else 0.08)."""
    if subbasin and subbasin.strip().lower() == "midland":
        return config.df_terminal_midland
    return config.df_terminal_per_year


def df_terminal_for_cohort(
    subbasins: Iterable[str | None], config: ForecastConfig
) -> float:
    """Terminal Df for a type-curve cohort's aggregate P50 fit.

    The cohort's P50 is a single aggregate series, so one Df must be
    imposed on the fit. Policy (of record): use Midland's shallower
    ``df_terminal_midland`` when Midland wells are the MAJORITY (> 50%) of
    the cohort, else the Delaware/default ``df_terminal_per_year`` — the
    per-well ``df_terminal_for_subbasin`` rule (Midland 0.06, else 0.08)
    lifted to the cohort grain. Empty cohort → the default.

    NULL / blank subbasins count toward the total but never as Midland,
    so a cohort that is only a plurality Midland (not a majority) keeps
    the default 0.08 — deliberately conservative about shallowing the
    tail (which raises EUR).
    """
    total = 0
    midland = 0
    for sb in subbasins:
        total += 1
        if sb and sb.strip().lower() == "midland":
            midland += 1
    if total > 0 and midland / total > 0.5:
        return config.df_terminal_midland
    return config.df_terminal_per_year


def detect_stream_peaks(
    monthly: pd.DataFrame,
) -> dict[str, PeakResult | None]:
    """Per-stream peak month for one well — each stream on its OWN rate.

    Water peaks hard in month 0-1 (flowback) months before oil; gas
    commonly peaks AFTER oil as the GOR climbs (39% of forecasted
    wells, p90 +4 months). Anchoring either on the oil peak reads the
    wrong qi and starts the fit slice on the wrong limb.

    A zero-rate "peak" (all-zero column — zeros aren't null, so
    detect_peak still returns index 0) isn't a real peak: the stream
    comes back None and the orchestrator SKIPS it. The old behavior —
    falling back to the oil anchor — fit zeros against oil-scale
    peak-anchored qi bounds and manufactured phantom forecasts.

    Pure function (takes a frame, no DB) so the per-stream rule is unit
    testable the same way ``cohort.classify_history`` is.
    """
    def _real(p: PeakResult | None) -> PeakResult | None:
        return p if (p is not None and p.peak_rate > 0) else None

    return {
        stream: _real(detect_peak(monthly, rate_column=col))
        for stream, col in _PEAK_RATE_COLUMN.items()
    }


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
    df = pd.DataFrame(rows, columns=[
        "prod_date", "oil_bbl", "gas_mcf", "water_bbl", "producing_days",
        "rate_calday_bopd", "rate_calday_mcfd", "rate_calday_bwpd",
    ])
    # Coalesce NULL monthly volumes / rates to 0. Raw warehouse production
    # carries reporting-gap months — a real calendar row where a stream's
    # volume + rate are NULL (operator filed a partial report; common on
    # some NM wells). A single such gap is catastrophic for the cum-fit:
    # `_post_peak_slice` does `vol_col.cumsum()`, NaN poisons the cumulative
    # array from the gap forward, and scipy's curve_fit raises
    # "array must not contain infs or NaNs". The orchestrator's broad
    # except swallows that as a fit failure, so the well silently gets NO
    # forecast row for that stream — surfacing later as a "missing" well in
    # the TC workspace that the Forecast button can't repair (it re-runs
    # this same path and fails identically). Treat a gap as zero reported
    # production: the cum stays calendar-aligned (the gap contributes
    # nothing), the zero-rate month is then dropped by the downtime filter,
    # and the fit proceeds. Wells with no gaps are unaffected (no-op fill).
    _numeric = [
        "oil_bbl", "gas_mcf", "water_bbl", "producing_days",
        "rate_calday_bopd", "rate_calday_mcfd", "rate_calday_bwpd",
    ]
    df[_numeric] = df[_numeric].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    return df


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
        select(Forecast).where(
            Forecast.api10 == api10, Forecast.stream == Stream(stream)
        )
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
    stmt = stmt.on_conflict_do_update(
        constraint="uq_forecasts_api10_stream", set_=update_cols
    )
    session.execute(stmt)
    session.commit()
    return values.get("id") or existing.id  # type: ignore[union-attr]


def forecast_well(
    session: Session,
    api10: str,
    *,
    config: ForecastConfig | None = None,
    persist: bool = True,
) -> dict[str, ForecastResult | None]:
    """Fit all three streams for a single well.

    Oil and gas anchor on the oil peak; water anchors on its own peak
    (see ``detect_stream_peaks``). Each stream's ramp prefix is anchored
    on its own ONSET (first producing month) rather than the well's
    first-prod, so leading zero / sub-floor months don't inflate the ramp
    length or the type-curve timing (``onset_index_months`` records the
    offset; see ``detect_onset``). Returns a dict {stream: ForecastResult
    or None on fit failure}. When `persist=True` (default), each
    successful result is written to the forecasts table via upsert.
    """
    cfg = config or ForecastConfig()
    # Basin-aware terminal Df: Midland wells get the shallower tail. Look
    # up the well's sub-basin once and bake the chosen Df into the config
    # used for every stream's fit + EUR.
    subbasin = session.execute(
        select(Well.subbasin).where(Well.api10 == api10)
    ).scalar_one_or_none()
    cfg = replace(cfg, df_terminal_per_year=df_terminal_for_subbasin(subbasin, cfg))

    monthly = _load_monthly(session, api10)
    out: dict[str, ForecastResult | None] = {"oil": None, "gas": None, "water": None}
    if monthly.empty:
        log.warning("no_production", api10=api10)
        return out

    # Per-stream peak: every stream anchors on its own (gas commonly
    # peaks after oil; water before — see detect_stream_peaks).
    peaks = detect_stream_peaks(monthly)
    if all(p is None for p in peaks.values()):
        log.warning("no_stream_peaks", api10=api10)
        return out

    # Default "rate_cum" runs the wrapper that retries with rate-time
    # when Di pins at a bound (cum-fit's low b-sensitivity often leaves
    # b at 1.0 and absorbs misfit into Di). Explicit "rate_time" or
    # "rate_cum_strict" opt out — useful for tests and per-well overrides.
    if cfg.fit_method == "rate_time":
        fit_fn = fit_rate_time
    elif cfg.fit_method == "rate_cum_strict":
        fit_fn = fit_rate_cum
    else:
        fit_fn = fit_with_fallback

    for stream in STREAMS:
        # Each stream is fit and ramp-anchored against ITS OWN peak;
        # peak_index_months (the ramp length) is per-stream. A stream
        # with no real production has no peak — skip it (no forecast
        # row) rather than fit zeros against another stream's anchor.
        peak = peaks[stream]
        if peak is None:
            log.info("no_stream_production_skip", api10=api10, stream=stream)
            continue
        peak_index_abs = int(peak.peak_index)
        try:
            result = fit_fn(
                monthly,
                model_type=cfg.model_type,
                peak=peak,
                stream=stream,
                config=cfg,
            )
        except Exception as e:
            log.exception("fit_failed", api10=api10, stream=stream, err=str(e))
            continue

        # Onset: trim leading sub-floor months so the ramp anchors at the
        # stream's first PRODUCING month, not the well's first-prod. Without
        # this, a delayed-onset / leading-zero stream (water flowback that
        # starts months late, or simply unreported early months) inflates
        # peak_index_months by those phantom months and pushes the
        # type-curve peak timing later. onset is <= peak by construction.
        floor = getattr(cfg, STREAM_DOWNTIME_FLOOR_FIELD[stream])
        onset_index = min(
            detect_onset(monthly, rate_column=_STREAM_RATE_COL[stream], floor=floor),
            peak_index_abs,
        )
        # Ramp length and qo are now ONSET-relative: ramp from the onset
        # rate up to qi over (peak - onset) months. onset_index_months
        # records the offset from well first-prod so the chart / TC can
        # place the curve on the right month.
        peak_index_months = peak_index_abs - onset_index
        qo = _stream_rate_at_index(monthly, onset_index, stream)

        # Stamp the ramp params. params dict also carries them so the
        # evaluator can pick them up without the row-level fields.
        # EUR is recomputed as ramp_eur + arps_eur to match the
        # ramp+Arps model — _build_result's Arps-only EUR was right
        # for the fit math but doesn't reflect the full forecast.
        new_params = dict(result.params)
        if qo is not None:
            new_params["qo"] = qo
        if peak_index_months > 0:
            new_params["peak_index_months"] = peak_index_months
        if onset_index > 0:
            new_params["onset_index_months"] = onset_index
        total_eur = compute_total_eur(
            model_type=result.model_type,
            params=new_params,
            horizon_years=cfg.horizon_years,
            economic_limit=getattr(cfg, STREAM_ECON_LIMIT_FIELD[stream]),
        )
        result = replace(
            result,
            params=new_params,
            qo=qo,
            peak_index_months=peak_index_months if peak_index_months > 0 else None,
            eur=total_eur,
        )

        out[stream] = result
        if persist:
            _persist(session, api10=api10, stream=stream, result=result)

    return out


_STREAM_RATE_COL: dict[str, str] = {
    "oil": "rate_calday_bopd",
    "gas": "rate_calday_mcfd",
    "water": "rate_calday_bwpd",
}


def stream_rate_at_peak(
    monthly: pd.DataFrame, peak: PeakResult, stream: str
) -> float:
    """Return the stream's calday rate at ``peak``'s month.

    Public helper — the cohort-transfer endpoint anchors short-history
    wells' qi with this same value, so the semantic needs to match the
    per-stream qi the autoforecast picks. Pass the peak the stream is
    anchored on (oil peak for oil/gas, water peak for water). 0.0 when
    the row is missing or the rate is null.
    """
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if peak.peak_index >= len(df):
        return 0.0
    val = df.iloc[peak.peak_index][_STREAM_RATE_COL[stream]]
    return float(val) if val is not None and not pd.isna(val) else 0.0


def _stream_rate_at_index(
    monthly: pd.DataFrame, index: int, stream: str
) -> float | None:
    """Return the stream's calday rate at ``index`` (0-based, chronological).

    Anchors ``qo`` at the stream's onset month rather than the well's
    first-prod month. ``None`` when out of range or null."""
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if index < 0 or index >= len(df):
        return None
    val = df.iloc[index][_STREAM_RATE_COL[stream]]
    return float(val) if val is not None and not pd.isna(val) else None


def stream_rate_at_first_prod(monthly: pd.DataFrame, stream: str) -> float | None:
    """Return the stream's calday rate at the well's first-prod month.

    Anchors the ramp prefix's ``qo`` value — the rate at the start of
    the well's life, before the ramp-up to peak. ``None`` when the
    first row is missing or the rate is null, which the evaluator
    treats as "no ramp data available; fall back to pure Arps." See
    app.forecasting.ramp_arps.evaluate_well_rate.
    """
    df = monthly.sort_values("prod_date").reset_index(drop=True)
    if df.empty:
        return None
    val = df.iloc[0][_STREAM_RATE_COL[stream]]
    if val is None or pd.isna(val):
        return None
    return float(val)


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
