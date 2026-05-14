"""Peak-month detection.

Rule (from the brief):

    Compute a 3-month centered rolling max on rate_calday_bopd. The peak
    month is the month where that rolling max is highest, restricted to
    the first 12 months of production. A single late-life rate spike (e.g.
    flush production from an offset frac in month 18) must NOT win.

    Gas and water streams inherit oil's peak month — we don't detect a
    separate peak per stream, because the streams need to share a common
    t=0 for forecasts and type-curve aggregation to be coherent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

PEAK_DETECTION_WINDOW_MONTHS: int = 3
PEAK_DETECTION_WINDOW_LIMIT_MONTHS: int = 12


@dataclass(frozen=True)
class PeakResult:
    peak_month_date: date
    peak_rate: float  # rate_calday_bopd AT the peak month (not the rolling-max value)
    peak_index: int   # 0-based index into the sorted monthly series


def detect_oil_peak(
    monthly_df: pd.DataFrame,
    *,
    rate_column: str = "rate_calday_bopd",
    date_column: str = "prod_date",
    window_months: int = PEAK_DETECTION_WINDOW_MONTHS,
    limit_months: int = PEAK_DETECTION_WINDOW_LIMIT_MONTHS,
) -> PeakResult | None:
    """Identify the peak production month from the oil rate series.

    Returns None for empty or all-null series. Caller is responsible for
    passing a frame whose `rate_column` is oil (per the brief — gas and
    water inherit oil's peak).
    """
    if monthly_df.empty or rate_column not in monthly_df.columns:
        return None
    df = monthly_df.sort_values(date_column).reset_index(drop=True)
    series = df[rate_column].astype(float)

    # `center=True` makes the window [t-1, t, t+1]; min_periods=1 lets the
    # first and last months use a partial window rather than dropping out.
    rolling = series.rolling(window=window_months, center=True, min_periods=1).max()
    candidates = rolling.iloc[:limit_months]
    if candidates.empty or candidates.isna().all():
        return None

    # The rolling max can put the same maximum value on several adjacent
    # months (e.g. months 0, 1, 2 all see the same high-rate window when
    # month 1 is the true peak). `idxmax` would return the FIRST tied
    # index — which is wrong for any well whose month 0 is a partial
    # ramp-up. Among tied indices, pick the one with the highest ACTUAL
    # rate. That ties back to a real production month, not an adjacency.
    max_smoothed = candidates.max()
    # Allow a small absolute tolerance in case rolling-max has float noise.
    tol = max(abs(max_smoothed) * 1e-9, 1e-9)
    tied_indices = candidates[(max_smoothed - candidates).abs() <= tol].index.tolist()
    peak_idx = int(max(tied_indices, key=lambda i: series.iloc[i]))

    peak_date_raw = df.iloc[peak_idx][date_column]
    peak_date: date = peak_date_raw.date() if hasattr(peak_date_raw, "date") else peak_date_raw

    return PeakResult(
        peak_month_date=peak_date,
        peak_rate=float(series.iloc[peak_idx]),
        peak_index=peak_idx,
    )
