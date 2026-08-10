"""Pull WellSeries records out of Postgres for type-curve aggregation.

Glue between the DB (`wells`, `production_monthly`, `forecasts`) and the
pure aggregation in `aggregate.py`.

Three alignments supported:
  * `peak_ramp` (default for new curves) — every well's PEAK sits at a
      common month index M = the cohort-median ramp length (per
      stream), with each well's own ramp occupying the months before
      it. Fixes the staggered-peak smearing of `first_prod_month`
      (cross-well percentiles never see all peaks in the same month →
      qi suppressed, early decline flattened → Di understated) while
      still carrying real ramp months for economics / facility
      buildout. Wells with ramps shorter than M contribute nulls
      before their onset; wells with longer ramps lose their earliest
      ramp month(s) off the front of the panel.
  * `first_prod_month` — t=0 at the stream's onset. Includes the ramp,
      but staggered peaks smear qi/Di — kept for back-compat with
      curves saved under it.
  * `peak_month` — t=0 at `forecast.peak_month_date` (oil-stream peak).
      Pure decline analysis; NO ramp months in the panel.
Both require the well to have an oil forecast — that's how we enforce
the "engineer reviewed fit quality before aggregating" workflow.

Two loaders:
  * `load_well_series` — observed rates only. Used for the workspace's
    empirical QC overlay (`observed_streams` block in the persisted
    series JSONB).
  * `load_wells_with_forecast` — ramp + Arps forecast end-to-end from
    each well's t=0 through 50 years. Override-aware. Engineer's per-
    well edits propagate into the TC aggregation; observed production
    no longer feeds the bands directly (only the per-well fit does).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Forecast, ProductionMonthly, Stream, TypeCurve, Well
from app.forecasting.peak_detection import onset_index_from_rates
from app.forecasting.ramp_arps import evaluate_well_rate
from app.forecasting.types import (
    DEFAULT_DOWNTIME_FLOOR_BOPD,
    DEFAULT_DOWNTIME_FLOOR_BWPD,
    DEFAULT_DOWNTIME_FLOOR_MCFD,
)
from app.type_curves.aggregate import AlignmentMethod, WellSeries

# Per-stream onset floor for the observed overlay's leading-zero trim
# under first_prod alignment. Mirrors ForecastConfig.downtime_floor_*.
_ONSET_FLOOR: dict[str, float] = {
    "oil": DEFAULT_DOWNTIME_FLOOR_BOPD,
    "gas": DEFAULT_DOWNTIME_FLOOR_MCFD,
    "water": DEFAULT_DOWNTIME_FLOOR_BWPD,
}


def _peak_month_by_api10(
    session: Session, api10s: list[str], stream: Stream = Stream.OIL
) -> dict[str, date]:
    """`peak_month_date` per api10 for the given stream, from forecasts.

    Defaults to oil (the gate stream — a well needs an oil forecast to be
    aggregatable). Water is fetched separately so the observed overlay
    can re-align to the water peak under peak_month alignment; water
    peaks months before oil, so sharing the oil anchor smears the water
    band. See ``_stream_slice_starts``.
    """
    if not api10s:
        return {}
    rows = session.execute(
        select(Forecast.api10, Forecast.peak_month_date)
        .where(Forecast.api10.in_(api10s))
        .where(Forecast.stream == stream)
    ).all()
    return {r.api10: r.peak_month_date for r in rows if r.peak_month_date is not None}


def _stream_slice_starts(
    *,
    alignment: AlignmentMethod,
    first_prod_date: date | None,
    oil_peak_date: date | None,
    gas_peak_date: date | None,
    water_peak_date: date | None,
) -> dict[str, date | None]:
    """Per-stream t=0 date for the observed overlay.

    Under ``first_prod_month`` every stream starts at first prod (the
    well's single calendar t=0). Under ``peak_month`` each stream
    starts at its OWN peak (gas commonly peaks after oil; water before)
    — falling back to the oil peak when the stream has no peak on file
    (rows that pre-date per-stream peaks and aren't re-fit yet). Pure
    function so the per-stream rule is unit testable.
    """
    if alignment in ("first_prod_month", "peak_ramp"):
        # peak_ramp also fetches from first prod — the onset trim and
        # the per-well pad to the common peak index happen downstream.
        return dict.fromkeys(("oil", "gas", "water"), first_prod_date)
    return {
        "oil": oil_peak_date,
        "gas": gas_peak_date if gas_peak_date is not None else oil_peak_date,
        "water": water_peak_date if water_peak_date is not None else oil_peak_date,
    }


def _first_index_on_or_after(prod_dates: list[date], target: date | None) -> int:
    """Index of the first row on/after ``target`` (0 when target is None).

    Rows are chronological. Used to convert a per-stream start date into
    an offset into a production list fetched from the earliest start, so
    each stream's array begins at its own t=0 even when streams are
    sliced from different months."""
    if target is None:
        return 0
    for i, d in enumerate(prod_dates):
        if d >= target:
            return i
    return len(prod_dates)


def _well_attrs_by_api10(
    session: Session, api10s: list[str]
) -> dict[str, tuple[float | None, float | None, date | None]]:
    """Returns {api10: (lateral_ft, proppant_lbs, first_prod_date)}."""
    if not api10s:
        return {}
    rows = session.execute(
        select(Well.api10, Well.lateral_ft, Well.proppant_lbs, Well.first_prod_date).where(
            Well.api10.in_(api10s)
        )
    ).all()
    return {r.api10: (r.lateral_ft, r.proppant_lbs, r.first_prod_date) for r in rows}


def _production_from(
    session: Session, api10: str, start: date
) -> list[tuple[date, float | None, float | None, float | None]]:
    """Return (prod_date, oil_rate, gas_rate, water_rate) calday tuples
    from `start` forward, in chronological order. The date rides along so
    callers can offset each stream to its own peak (see load_well_series)."""
    rows = session.execute(
        select(
            ProductionMonthly.prod_date,
            ProductionMonthly.rate_calday_bopd,
            ProductionMonthly.rate_calday_mcfd,
            ProductionMonthly.rate_calday_bwpd,
        )
        .where(ProductionMonthly.api10 == api10)
        .where(ProductionMonthly.prod_date >= start)
        .order_by(ProductionMonthly.prod_date)
    ).all()
    return [
        (
            r.prod_date,
            float(r.rate_calday_bopd) if r.rate_calday_bopd is not None else None,
            float(r.rate_calday_mcfd) if r.rate_calday_mcfd is not None else None,
            float(r.rate_calday_bwpd) if r.rate_calday_bwpd is not None else None,
        )
        for r in rows
    ]


def load_well_series(
    session: Session,
    api10s: Iterable[str],
    *,
    alignment: AlignmentMethod = "first_prod_month",
    ramp_anchors: dict[str, int] | None = None,
) -> list[WellSeries]:
    """Build the inputs the aggregator expects from the DB.

    Wells without an oil forecast (so no known peak month) are skipped
    regardless of alignment — you can't include a well in a type curve
    before forecasting it. For `first_prod_month` alignment, wells also
    need a non-null `first_prod_date` in the wells table.

    Each stream is sliced from its OWN t=0 so the observed overlay lines
    up with the per-stream-anchored forecast bands: under `peak_month`
    from its own peak (oil/gas from the oil peak, water from the water
    peak); under `first_prod_month` from its own ONSET (first producing
    month), which trims leading zero / sub-floor months so a delayed-onset
    stream re-zeros to its onset. This makes the per-stream arrays ragged
    in length; the aggregator sizes its panel to the longest stream.
    """
    api10_list = list(api10s)
    oil_peaks = _peak_month_by_api10(session, api10_list, Stream.OIL)
    gas_peaks = _peak_month_by_api10(session, api10_list, Stream.GAS)
    water_peaks = _peak_month_by_api10(session, api10_list, Stream.WATER)
    attrs = _well_attrs_by_api10(session, api10_list)

    out: list[WellSeries] = []
    for api10 in api10_list:
        # Oil forecast must exist regardless of alignment (the gate).
        if api10 not in oil_peaks:
            continue
        lateral_ft, proppant_lbs, first_prod_date = attrs.get(api10, (None, None, None))
        if alignment == "first_prod_month" and first_prod_date is None:
            continue

        starts = _stream_slice_starts(
            alignment=alignment,
            first_prod_date=first_prod_date,
            oil_peak_date=oil_peaks[api10],
            gas_peak_date=gas_peaks.get(api10),
            water_peak_date=water_peaks.get(api10),
        )
        start_dates = [d for d in starts.values() if d is not None]
        if not start_dates:
            continue
        # Fetch once from the earliest per-stream start, then offset each
        # stream to its own t=0. Under first_prod alignment all three
        # starts are equal, so every offset is 0 (behavior unchanged).
        prod = _production_from(session, api10, min(start_dates))
        if not prod:
            continue
        prod_dates = [r[0] for r in prod]
        oil_full = [r[1] for r in prod]
        gas_full = [r[2] for r in prod]
        wat_full = [r[3] for r in prod]
        if alignment in ("first_prod_month", "peak_ramp"):
            # Trim each stream to its ONSET (first producing month) so the
            # observed overlay matches the onset-anchored forecast bands —
            # a delayed-onset stream (water with leading zeros) re-zeros to
            # its onset instead of carrying phantom leading-zero months.
            oil_off = onset_index_from_rates(oil_full, floor=_ONSET_FLOOR["oil"])
            gas_off = onset_index_from_rates(gas_full, floor=_ONSET_FLOOR["gas"])
            wat_off = onset_index_from_rates(wat_full, floor=_ONSET_FLOOR["water"])
        else:  # peak_month — each stream sliced from its own peak
            oil_off = _first_index_on_or_after(prod_dates, starts["oil"])
            gas_off = _first_index_on_or_after(prod_dates, starts["gas"])
            wat_off = _first_index_on_or_after(prod_dates, starts["water"])

        oil_rates = oil_full[oil_off:]
        gas_rates = gas_full[gas_off:]
        wat_rates = wat_full[wat_off:]

        if alignment == "peak_ramp":
            # Slide each stream so its PEAK lands on the cohort anchor
            # M — same mechanism as the forecast loader, so the
            # observed overlay sits on the bands. m_w = months from
            # onset to the forecast's peak month; pad (or trim) the
            # front by M - m_w.
            anchors = ramp_anchors or {}

            def _to_anchor(
                rates: list[float | None], off: int, peak_date: date | None, stream: str
            ) -> list[float | None]:
                m_w = max(0, _first_index_on_or_after(prod_dates, peak_date) - off)
                pad = int(anchors.get(stream, 0)) - m_w
                if pad >= 0:
                    return [None] * pad + rates
                return rates[-pad:]

            oil_rates = _to_anchor(oil_rates, oil_off, oil_peaks.get(api10), "oil")
            gas_rates = _to_anchor(
                gas_rates,
                gas_off,
                gas_peaks.get(api10) or oil_peaks.get(api10),
                "gas",
            )
            wat_rates = _to_anchor(
                wat_rates,
                wat_off,
                water_peaks.get(api10) or oil_peaks.get(api10),
                "water",
            )

        out.append(
            WellSeries(
                api10=api10,
                lateral_ft=lateral_ft,
                proppant_lbs=proppant_lbs,
                oil_rates=oil_rates,
                gas_rates=gas_rates,
                water_rates=wat_rates,
            )
        )
    return out


# ============ Forecast-aggregation loader (Option B) ============
#
# For each included well, the per-stream rate array fed to the
# aggregator is built as:
#
#   • months 0..peak_offset    — OBSERVED from production_monthly
#                                (the pre-peak ramp; only non-empty
#                                under first_prod_month alignment)
#   • months peak_offset+1..N  — ANALYTIC from the resolved forecast
#                                (override → global). Modified
#                                hyperbolic decline anchored at peak.
#
# Wells without a resolved forecast for a given stream get all-null
# rates for that stream (no contribution to the band). Wells without
# ANY stream resolved are dropped entirely.
#
# Aggregation horizon defaults to 600 months (50 yr) so the bands
# match the existing per-percentile forecast convention. Output is a
# list[WellSeries] — same shape as ``load_well_series`` — so the
# existing aggregator + downstream code consumes it unchanged.


def _resolve_params(
    tc: TypeCurve,
    api10: str,
    stream: Stream,
    global_forecast: Forecast | None,
) -> dict[str, Any] | None:
    """Resolve qi/Di/b/Df + ramp prefix for one (well, stream).

    Override → global → none. Returns None when no fit is available
    so the caller can null that stream's rates. qo / peak_index_months
    come along when present (lets the caller render the ramp prefix
    via evaluate_well_rate); they're None for rows that pre-date the
    ramp columns and the evaluator falls back to pure Arps.
    """
    overrides = (tc.forecast_overrides or {}).get(api10) or {}
    override = overrides.get(stream.value)
    if override:
        params = override.get("params") or {}
        qi = params.get("qi", override.get("qi"))
        Di = params.get("Di", override.get("di_initial"))
        b = params.get("b", override.get("b"))
        Df = params.get("Df", override.get("df_terminal"))
        qo = params.get("qo", override.get("qo"))
        peak_index_months = params.get("peak_index_months", override.get("peak_index_months"))
    elif global_forecast is not None:
        params = global_forecast.params or {}
        qi = params.get("qi", global_forecast.qi)
        Di = params.get("Di", global_forecast.di_initial)
        b = params.get("b", global_forecast.b)
        Df = params.get("Df", global_forecast.df_terminal)
        qo = params.get("qo", global_forecast.qo)
        peak_index_months = params.get("peak_index_months", global_forecast.peak_index_months)
    else:
        return None
    # Skip anything non-finite — a bad fit shouldn't contaminate the
    # whole panel with NaNs. qo / peak_index_months stay optional —
    # missing ones flip the evaluator to pure Arps for that well.
    if any(v is None or not math.isfinite(float(v)) for v in (qi, Di, b, Df)):
        return None
    out: dict[str, Any] = {
        "qi": float(qi),
        "Di": float(Di),
        "b": float(b),
        "Df": float(Df),
    }
    if qo is not None and math.isfinite(float(qo)):
        out["qo"] = float(qo)
    if peak_index_months is not None and int(peak_index_months) >= 0:
        out["peak_index_months"] = int(peak_index_months)
    return out


def _forecast_rates(
    params: dict[str, Any] | None,
    n_months: int,
    *,
    include_ramp: bool,
    shift_months: int = 0,
) -> list[float | None]:
    """N-month rate trajectory from t=0 forward.

    ``include_ramp`` controls whether the ramp prefix is evaluated:
    True under first_prod_month / peak_ramp alignment, False under
    peak_month (t=0 is peak, no ramp segment to draw). When ramp params
    are missing from `params`, evaluate_well_rate falls back to pure
    Arps regardless.

    ``shift_months`` slides the well along the panel's month axis —
    the peak_ramp mechanism. Positive: the well starts that many
    months late (front-padded with nulls — there's no production
    signal before its onset). Negative: the well's earliest ramp
    months fall before the panel and are dropped. The caller passes
    ``M - peak_index_months`` so every well's peak lands on the common
    month M.
    """
    if params is None or n_months <= 0:
        return [None] * max(n_months, 0)
    lead = max(0, shift_months)
    t_years = (np.arange(n_months - lead, dtype=float) - min(shift_months, 0)) / 12.0
    qo = params.get("qo") if include_ramp else None
    peak_index_months = params.get("peak_index_months") if include_ramp else None
    rates = evaluate_well_rate(
        qo=qo,
        peak_index_months=peak_index_months,
        qi=params["qi"],
        Di=params["Di"],
        b=params["b"],
        Df=params["Df"],
        t_years=t_years,
    )
    return [None] * lead + [float(x) for x in rates]


def cohort_ramp_anchors(
    session: Session,
    tc: TypeCurve,
    api10s: Iterable[str],
) -> dict[str, int]:
    """Common peak month index M per stream for peak_ramp alignment.

    M = the cohort median of ``peak_index_months`` across the resolved
    (override → global) forecasts; wells whose fit carries no ramp
    count as 0. The median guarantees at least one well's ramp reaches
    back to panel month 0, so the ramp region always has data. Compute
    ONCE per aggregation and pass the same dict to both loaders so the
    forecast bands and the observed QC overlay line up.
    """
    api10_list = list(api10s)
    if not api10_list:
        return {"oil": 0, "gas": 0, "water": 0}
    forecast_rows = (
        session.execute(select(Forecast).where(Forecast.api10.in_(api10_list))).scalars().all()
    )
    by_key: dict[tuple[str, Stream], Forecast] = {(f.api10, f.stream): f for f in forecast_rows}
    anchors: dict[str, int] = {}
    for stream in (Stream.OIL, Stream.GAS, Stream.WATER):
        ramps: list[int] = []
        for api10 in api10_list:
            params = _resolve_params(tc, api10, stream, by_key.get((api10, stream)))
            if params is not None:
                ramps.append(int(params.get("peak_index_months") or 0))
        anchors[stream.value] = int(round(float(np.median(ramps)))) if ramps else 0
    return anchors


def load_wells_with_forecast(
    session: Session,
    tc: TypeCurve,
    api10s: Iterable[str],
    *,
    alignment: AlignmentMethod = "first_prod_month",
    n_months: int = 600,
    ramp_anchors: dict[str, int] | None = None,
) -> list[WellSeries]:
    """Build the WellSeries list for forecast-based aggregation.

    Each WellSeries' per-stream rate array is the ramp+Arps forecast
    evaluated from t=0 (well's anchor month for the chosen alignment)
    out to ``n_months``. Override-aware via ``tc.forecast_overrides``.

    Under ``first_prod_month`` alignment the ramp prefix is included
    (qo→qi over peak_index_months), so the aggregated early-time bands
    reflect per-well ramp behavior. Under ``peak_month`` alignment the
    ramp is omitted (t=0 is peak) and the trajectory is pure Arps from
    the peak forward.

    Wells without an oil forecast are dropped (consistent with the
    observed loader's gate). Under first_prod_month, wells also need a
    non-null ``first_prod_date`` so the panel has a well-defined t=0.
    """
    api10_list = list(api10s)
    peaks = _peak_month_by_api10(session, api10_list)
    attrs = _well_attrs_by_api10(session, api10_list)

    forecast_rows = (
        session.execute(select(Forecast).where(Forecast.api10.in_(api10_list))).scalars().all()
    )
    forecasts_by: dict[tuple[str, Stream], Forecast] = {
        (f.api10, f.stream): f for f in forecast_rows
    }

    include_ramp = alignment in ("first_prod_month", "peak_ramp")
    if alignment == "peak_ramp" and ramp_anchors is None:
        # Direct callers (CLI, tests) that didn't precompute the
        # anchors — derive them here. The API path passes them in so
        # the observed overlay uses the identical M.
        ramp_anchors = cohort_ramp_anchors(session, tc, api10_list)
    anchors = ramp_anchors or {}

    def _shift(params: dict[str, Any] | None, stream: str) -> int:
        if alignment != "peak_ramp" or params is None:
            return 0
        m_w = int(params.get("peak_index_months") or 0)
        return int(anchors.get(stream, 0)) - m_w

    out: list[WellSeries] = []
    for api10 in api10_list:
        if api10 not in peaks:
            continue
        lateral_ft, proppant_lbs, first_prod_date = attrs.get(api10, (None, None, None))
        if alignment == "first_prod_month" and first_prod_date is None:
            # No well-defined t=0 → can't aggregate this well in
            # first-prod-aligned mode. (peak_ramp anchors on the peak
            # index, not the calendar, so it has no such requirement.)
            continue

        oil_params = _resolve_params(tc, api10, Stream.OIL, forecasts_by.get((api10, Stream.OIL)))
        gas_params = _resolve_params(tc, api10, Stream.GAS, forecasts_by.get((api10, Stream.GAS)))
        wat_params = _resolve_params(
            tc, api10, Stream.WATER, forecasts_by.get((api10, Stream.WATER))
        )

        oil_rates = _forecast_rates(
            oil_params,
            n_months,
            include_ramp=include_ramp,
            shift_months=_shift(oil_params, "oil"),
        )
        gas_rates = _forecast_rates(
            gas_params,
            n_months,
            include_ramp=include_ramp,
            shift_months=_shift(gas_params, "gas"),
        )
        wat_rates = _forecast_rates(
            wat_params,
            n_months,
            include_ramp=include_ramp,
            shift_months=_shift(wat_params, "water"),
        )

        out.append(
            WellSeries(
                api10=api10,
                lateral_ft=lateral_ft,
                proppant_lbs=proppant_lbs,
                oil_rates=oil_rates,
                gas_rates=gas_rates,
                water_rates=wat_rates,
            )
        )
    return out
