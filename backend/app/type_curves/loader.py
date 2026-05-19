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
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Forecast, ProductionMonthly, Stream, Well
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
