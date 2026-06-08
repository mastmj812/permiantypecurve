"""Peak-month detection.

Rule (from the brief):

    Compute a 3-month centered rolling max on the stream's calday rate.
    The peak month is the month where that rolling max is highest,
    restricted to the first 12 months of production. A single late-life
    rate spike (e.g. flush production from an offset frac in month 18)
    must NOT win.

    Gas inherits oil's peak month — we don't detect a separate gas peak,
    because gas tracks oil closely enough that a shared t=0 keeps the
    forecasts and TC aggregation coherent. WATER, however, gets its own
    peak: Permian flowback water peaks hard in month 0-1 and declines
    immediately, months before oil ramps to its peak. Anchoring water on
    the oil peak reads a low qi (the water peak has already decayed) and
    a shallow Di (the steep early decline is upstream of the fit slice).
    See app.forecasting.orchestrator.detect_stream_peaks for the wiring.

    ``detect_peak`` is stream-agnostic — pass the stream's rate column.
    ``detect_oil_peak`` is the oil-defaulted wrapper kept for callers
    that only ever look at oil (TC P50 fit, cohort history classifier).
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


def detect_peak(
    monthly_df: pd.DataFrame,
    *,
    rate_column: str = "rate_calday_bopd",
    date_column: str = "prod_date",
    window_months: int = PEAK_DETECTION_WINDOW_MONTHS,
    limit_months: int = PEAK_DETECTION_WINDOW_LIMIT_MONTHS,
) -> PeakResult | None:
    """Identify the peak production month from a stream's rate series.

    Stream-agnostic: pass ``rate_column`` for the stream you want
    (``rate_calday_bopd`` / ``_mcfd`` / ``_bwpd``). Returns None for an
    empty frame, a missing column, or an all-null series. The returned
    ``peak_rate`` is in the units of ``rate_column``.
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


def detect_oil_peak(
    monthly_df: pd.DataFrame,
    *,
    rate_column: str = "rate_calday_bopd",
    date_column: str = "prod_date",
    window_months: int = PEAK_DETECTION_WINDOW_MONTHS,
    limit_months: int = PEAK_DETECTION_WINDOW_LIMIT_MONTHS,
) -> PeakResult | None:
    """Oil-defaulted ``detect_peak``. Kept as the entry point for callers
    that are oil-only by design (TC P50 series fit, cohort history
    classification). The ``rate_column`` kwarg is preserved so existing
    callers that passed a non-oil column keep working unchanged."""
    return detect_peak(
        monthly_df,
        rate_column=rate_column,
        date_column=date_column,
        window_months=window_months,
        limit_months=limit_months,
    )
