"""Cohort partitioning and donor-parameter helpers for the short-history
transfer workflow.

A well's autoforecast Di / b is unreliable when there's little post-peak
production to constrain decline. The fit lands on a low-Di / high-EUR
solution because the cumulative-vs-time NLS objective has no curvature to
resolve. Rather than tighten the bounds (which papers over the missing
data), we split the user's batch into a "long-history" group and a
"short-history" group, fit only the long group, and then write transfer
forecasts for the short group using the long group's median Di / b plus
each short well's own peak rate as qi.

Public entry points:

  * ``partition_by_history`` — split a batch by post-peak history length.
  * ``compute_donor_medians`` — median Di / b across a donor forecast list.

Both keep the math in one place so the batch endpoint and the transfer
endpoint apply the exact same definitions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Forecast, ProductionMonthly, Stream
from app.forecasting.peak_detection import detect_oil_peak


@dataclass(frozen=True)
class HistoryPartition:
    """Result of splitting a batch by post-peak history length.

    ``no_peak`` lists wells with no detectable oil peak (still ramping or
    no production data). They are neither long nor short — there's no
    qi to anchor a transfer forecast to. The transfer endpoint surfaces
    them as ``skipped_no_peak`` so the user can intervene.
    """

    long_api10s: list[str]
    short_api10s: list[str]
    no_peak_api10s: list[str]


def _post_peak_months(monthly_df: pd.DataFrame, peak_index: int) -> int:
    """Count of months from the peak forward, inclusive of the peak month.

    Matches the convention in ``fit._post_peak_slice`` (the row at
    ``peak_index`` is included). Downtime filtering is intentionally NOT
    applied here — the cutoff means "how much history exists," not "how
    much history survives the downtime filter." A well that's been
    producing for 12 months with 4 of them flagged as downtime is still
    a 12-month well from the user's perspective.
    """
    return max(0, len(monthly_df) - peak_index)


def _load_monthly(session: Session, api10: str) -> pd.DataFrame:
    rows = session.execute(
        select(
            ProductionMonthly.prod_date,
            ProductionMonthly.rate_calday_bopd,
        )
        .where(ProductionMonthly.api10 == api10)
        .order_by(ProductionMonthly.prod_date)
    ).all()
    return pd.DataFrame(rows, columns=["prod_date", "rate_calday_bopd"])


def classify_history(monthly: pd.DataFrame, cutoff_months: int) -> str:
    """Pure-function classifier — returns ``"long" | "short" | "no_peak"``.

    Lifted out of ``partition_by_history`` so the boundary semantic
    (equal-to-cutoff is long; no-peak well skips transfer) can be unit
    tested without a database session.
    """
    if monthly.empty:
        return "no_peak"
    peak = detect_oil_peak(monthly)
    if peak is None:
        return "no_peak"
    n = _post_peak_months(monthly, peak.peak_index)
    return "long" if n >= cutoff_months else "short"


def partition_by_history(
    session: Session,
    api10s: list[str],
    cutoff_months: int,
) -> HistoryPartition:
    """Split ``api10s`` into long / short / no-peak buckets by oil history.

    A well lands in ``long`` when its post-peak month count is
    ``>= cutoff_months``. Equal-to-cutoff is "long" — at the boundary
    we trust the fit. Wells with no detectable oil peak (empty
    production or all-null rates inside the 12-month peak window) go
    into ``no_peak`` and skip the transfer entirely; the user has to
    wait for more data or hand-build a forecast.
    """
    buckets: dict[str, list[str]] = {"long": [], "short": [], "no_peak": []}
    for api10 in api10s:
        monthly = _load_monthly(session, api10)
        buckets[classify_history(monthly, cutoff_months)].append(api10)
    return HistoryPartition(
        long_api10s=buckets["long"],
        short_api10s=buckets["short"],
        no_peak_api10s=buckets["no_peak"],
    )


@dataclass(frozen=True)
class DonorMedians:
    di: float
    b: float
    donor_count: int
    donor_api10s: list[str]


def compute_donor_medians(donors: list[Forecast]) -> DonorMedians | None:
    """Median Di and b across the supplied oil donor forecasts.

    ``donors`` should be the oil-stream Forecast rows for the long
    cohort, already filtered to those with non-null Di and b. Returns
    ``None`` when there are no usable donors — the transfer endpoint
    converts that into a 422.

    Why median rather than mean: medians shrug off outlier donors
    without requiring per-well exclusion plumbing. The user already
    has the per-well edit / lock UI to handle individual wonky fits;
    this just keeps a single edge case from dragging the cohort.
    """
    valid = [
        f for f in donors
        if f.di_initial is not None and f.b is not None
    ]
    if not valid:
        return None
    di_arr = np.array([f.di_initial for f in valid], dtype=float)
    b_arr = np.array([f.b for f in valid], dtype=float)
    return DonorMedians(
        di=float(np.median(di_arr)),
        b=float(np.median(b_arr)),
        donor_count=len(valid),
        donor_api10s=[f.api10 for f in valid],
    )


def load_stream_donors(
    session: Session, long_api10s: list[str], stream: Stream
) -> list[Forecast]:
    """Fetch the per-stream Forecast rows for the long-history cohort.

    Whether each row came from a fresh autoforecast or the user's
    manual edit is irrelevant — the resolved ``params`` are the truth.
    Locked rows count too: a locked well is one the user has signed
    off on, which is exactly what we want in the donor pool.
    """
    if not long_api10s:
        return []
    return list(
        session.execute(
            select(Forecast).where(
                Forecast.api10.in_(long_api10s),
                Forecast.stream == stream,
            )
        ).scalars().all()
    )
