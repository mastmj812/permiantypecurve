"""Pull WellSeries records out of Postgres for type-curve aggregation.

Glue between the DB (`wells`, `production_monthly`, `forecasts`) and the
pure aggregation in `aggregate.py`.

Two alignments supported:
  * `first_prod_month` (default) — slice from `wells.first_prod_date`.
      The standard Permian operator practice; includes 1–3 months of
      ramp-up before peak. Use this for economics / DCF modeling.
  * `peak_month` — slice from `forecast.peak_month_date` (oil-stream peak).
      Use for pure decline-curve analysis where well-to-well decline
      patterns matter more than absolute time-zero.
Both require the well to have an oil forecast — that's how we enforce
the "engineer reviewed fit quality before aggregating" workflow.

Two loaders:
  * `load_well_series` — observed rates only. Used for the workspace's
    empirical QC overlay (`observed_streams` block in the persisted
    series JSONB).
  * `load_wells_with_forecast` — observed pre-peak ramp + analytic
    post-peak forecast (override → global), out to 50 years. Drives
    the canonical bands + fitted P50. Override edits change the
    output and therefore the type curve.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Forecast, ProductionMonthly, Stream, TypeCurve, Well
from app.type_curves.aggregate import AlignmentMethod, WellSeries


def _peak_month_by_api14(
    session: Session, api14s: list[str]
) -> dict[str, date]:
    """Oil-stream peak_month_date per api14, from the forecasts table."""
    if not api14s:
        return {}
    rows = session.execute(
        select(Forecast.api14, Forecast.peak_month_date)
        .where(Forecast.api14.in_(api14s))
        .where(Forecast.stream == Stream.OIL)
    ).all()
    return {r.api14: r.peak_month_date for r in rows if r.peak_month_date is not None}


def _well_attrs_by_api14(
    session: Session, api14s: list[str]
) -> dict[str, tuple[float | None, float | None, date | None]]:
    """Returns {api14: (lateral_ft, proppant_lbs, first_prod_date)}."""
    if not api14s:
        return {}
    rows = session.execute(
        select(Well.api14, Well.lateral_ft, Well.proppant_lbs, Well.first_prod_date)
        .where(Well.api14.in_(api14s))
    ).all()
    return {r.api14: (r.lateral_ft, r.proppant_lbs, r.first_prod_date) for r in rows}


def _production_from(
    session: Session, api14: str, start: date
) -> list[tuple[float | None, float | None, float | None]]:
    """Return (oil_rate, gas_rate, water_rate) calday tuples from `start`
    forward, in chronological order."""
    rows = session.execute(
        select(
            ProductionMonthly.rate_calday_bopd,
            ProductionMonthly.rate_calday_mcfd,
            ProductionMonthly.rate_calday_bwpd,
        )
        .where(ProductionMonthly.api14 == api14)
        .where(ProductionMonthly.prod_date >= start)
        .order_by(ProductionMonthly.prod_date)
    ).all()
    return [
        (
            float(r.rate_calday_bopd) if r.rate_calday_bopd is not None else None,
            float(r.rate_calday_mcfd) if r.rate_calday_mcfd is not None else None,
            float(r.rate_calday_bwpd) if r.rate_calday_bwpd is not None else None,
        )
        for r in rows
    ]


def load_well_series(
    session: Session,
    api14s: Iterable[str],
    *,
    alignment: AlignmentMethod = "first_prod_month",
) -> list[WellSeries]:
    """Build the inputs the aggregator expects from the DB.

    Wells without an oil forecast (so no known peak month) are skipped
    regardless of alignment — you can't include a well in a type curve
    before forecasting it. For `first_prod_month` alignment, wells also
    need a non-null `first_prod_date` in the wells table.
    """
    api14_list = list(api14s)
    peaks = _peak_month_by_api14(session, api14_list)
    attrs = _well_attrs_by_api14(session, api14_list)

    out: list[WellSeries] = []
    for api14 in api14_list:
        # Forecast must exist regardless of alignment.
        if api14 not in peaks:
            continue
        lateral_ft, proppant_lbs, first_prod_date = attrs.get(
            api14, (None, None, None)
        )

        if alignment == "first_prod_month":
            if first_prod_date is None:
                continue
            slice_from = first_prod_date
        else:  # "peak_month"
            slice_from = peaks[api14]

        prod = _production_from(session, api14, slice_from)
        if not prod:
            continue
        out.append(WellSeries(
            api14=api14,
            lateral_ft=lateral_ft,
            proppant_lbs=proppant_lbs,
            oil_rates=[r[0] for r in prod],
            gas_rates=[r[1] for r in prod],
            water_rates=[r[2] for r in prod],
        ))
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


def _eval_modified_hyperbolic_rate(
    t_years: float,
    qi: float,
    Di: float,
    b: float,
    Df: float,
) -> float:
    """Modified-hyperbolic rate at time ``t_years`` past peak.

    Mirrors ``app.forecasting.models.modified_hyperbolic`` but inlined
    here so this loader doesn't pull the whole forecasting package on
    each TC re-aggregation. Hyperbolic until the instantaneous
    nominal decline drops to ``Df``, then exponential.
    """
    if t_years <= 0:
        return qi
    if Df <= 0 or Di <= 0 or Df >= Di or abs(b) < 1e-6:
        b_safe = max(b, 1e-6)
        return qi / math.pow(1.0 + b_safe * Di * t_years, 1.0 / b_safe)
    t_switch = (Di / Df - 1.0) / (b * Di)
    if t_years <= t_switch:
        return qi / math.pow(1.0 + b * Di * t_years, 1.0 / b)
    q_switch = qi * math.pow(Df / Di, 1.0 / b)
    return q_switch * math.exp(-Df * (t_years - t_switch))


def _months_between(start: date, end: date) -> int:
    """Whole months from ``start`` to ``end`` (>= 0). Negative inputs
    clamp to 0 — a peak-before-first-prod date is data garbage and
    should not push the ramp window into the forecast."""
    diff = (end.year - start.year) * 12 + (end.month - start.month)
    return max(diff, 0)


def _resolve_params(
    tc: TypeCurve,
    api14: str,
    stream: Stream,
    global_forecast: Forecast | None,
) -> dict[str, float] | None:
    """Resolve qi/Di/b/Df for one (well, stream) following the override
    → global → none chain. Returns None when no fit is available so
    the caller can null that stream's rates."""
    overrides = (tc.forecast_overrides or {}).get(api14) or {}
    override = overrides.get(stream.value)
    if override:
        params = override.get("params") or {}
        qi = params.get("qi", override.get("qi"))
        Di = params.get("Di", override.get("di_initial"))
        b = params.get("b", override.get("b"))
        Df = params.get("Df", override.get("df_terminal"))
    elif global_forecast is not None:
        params = global_forecast.params or {}
        qi = params.get("qi", global_forecast.qi)
        Di = params.get("Di", global_forecast.di_initial)
        b = params.get("b", global_forecast.b)
        Df = params.get("Df", global_forecast.df_terminal)
    else:
        return None
    # Skip anything non-finite — a bad fit shouldn't contaminate the
    # whole panel with NaNs.
    if any(v is None or not math.isfinite(float(v)) for v in (qi, Di, b, Df)):
        return None
    return {"qi": float(qi), "Di": float(Di), "b": float(b), "Df": float(Df)}


def _forecast_rates(
    params: dict[str, float] | None, n_months_after_peak: int
) -> list[float | None]:
    """N-month rate trajectory from peak forward. ``None`` per month
    when params are missing (well had no forecast for this stream)."""
    if params is None or n_months_after_peak <= 0:
        return [None] * max(n_months_after_peak, 0)
    out: list[float | None] = []
    for k in range(1, n_months_after_peak + 1):
        out.append(
            _eval_modified_hyperbolic_rate(
                k / 12.0,
                params["qi"], params["Di"], params["b"], params["Df"],
            )
        )
    return out


def load_wells_with_forecast(
    session: Session,
    tc: TypeCurve,
    api14s: Iterable[str],
    *,
    alignment: AlignmentMethod = "first_prod_month",
    n_months: int = 600,
) -> list[WellSeries]:
    """Build the WellSeries list for forecast-based aggregation.

    Each WellSeries' per-stream rate array runs ``observed pre-peak +
    forecast post-peak`` out to ``n_months``. Override-aware via
    ``tc.forecast_overrides``. Wells without an oil forecast are
    dropped (consistent with the observed loader's gate). Wells with
    a forecast but missing peak metadata are also dropped — there's
    no way to splice observed pre-peak without it.
    """
    api14_list = list(api14s)
    peaks = _peak_month_by_api14(session, api14_list)
    attrs = _well_attrs_by_api14(session, api14_list)

    # Pre-load all stream forecasts in one trip. Resolver looks up by
    # (api14, stream) below.
    forecast_rows = session.execute(
        select(Forecast).where(Forecast.api14.in_(api14_list))
    ).scalars().all()
    forecasts_by: dict[tuple[str, Stream], Forecast] = {
        (f.api14, f.stream): f for f in forecast_rows
    }

    out: list[WellSeries] = []
    for api14 in api14_list:
        if api14 not in peaks:
            continue  # oil peak required — consistent with observed loader
        lateral_ft, proppant_lbs, first_prod_date = attrs.get(
            api14, (None, None, None)
        )
        peak_date = peaks[api14]

        # Peak offset = months of pre-peak ramp the panel needs to
        # carry. Zero under peak_month alignment (panel starts at peak).
        if alignment == "first_prod_month":
            if first_prod_date is None:
                continue
            peak_offset = _months_between(first_prod_date, peak_date)
            ramp_start = first_prod_date
        else:  # "peak_month"
            peak_offset = 0
            ramp_start = peak_date

        # Pull observed rates for the ramp window (months 0..peak_offset
        # inclusive — peak month itself is observed). Empty list when
        # peak_offset == 0.
        if peak_offset > 0:
            ramp_rates = _production_from(session, api14, ramp_start)[
                : peak_offset + 1
            ]
            # If production_monthly is sparse and didn't cover the full
            # ramp window, pad with None — _stream_panel ignores None.
            while len(ramp_rates) < peak_offset + 1:
                ramp_rates.append((None, None, None))
        else:
            # peak_month alignment: month 0 IS the peak; one observed
            # row so the panel anchors at the well's true peak rate
            # rather than the fit's qi (small drift on most fits).
            single = _production_from(session, api14, peak_date)[:1]
            ramp_rates = single if single else [(None, None, None)]

        # Forecast tail length: total horizon minus the ramp + peak month.
        tail_n = max(n_months - len(ramp_rates), 0)

        oil_params = _resolve_params(tc, api14, Stream.OIL, forecasts_by.get((api14, Stream.OIL)))
        gas_params = _resolve_params(tc, api14, Stream.GAS, forecasts_by.get((api14, Stream.GAS)))
        wat_params = _resolve_params(tc, api14, Stream.WATER, forecasts_by.get((api14, Stream.WATER)))

        oil_tail = _forecast_rates(oil_params, tail_n)
        gas_tail = _forecast_rates(gas_params, tail_n)
        wat_tail = _forecast_rates(wat_params, tail_n)

        oil_rates: list[float | None] = [r[0] for r in ramp_rates] + oil_tail
        gas_rates: list[float | None] = [r[1] for r in ramp_rates] + gas_tail
        wat_rates: list[float | None] = [r[2] for r in ramp_rates] + wat_tail

        out.append(
            WellSeries(
                api14=api14,
                lateral_ft=lateral_ft,
                proppant_lbs=proppant_lbs,
                oil_rates=oil_rates[:n_months],
                gas_rates=gas_rates[:n_months],
                water_rates=wat_rates[:n_months],
            )
        )
    return out
