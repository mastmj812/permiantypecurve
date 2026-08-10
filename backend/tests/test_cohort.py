"""Cohort partition + donor-median tests.

Covers the pure-function pieces of `app.forecasting.cohort`. The
DB-coupled `partition_by_history` is exercised end-to-end via the
batch endpoint manually — these tests pin the classification rule and
the median math so a regression there shows up fast.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from app.forecasting.cohort import (
    DonorMedians,
    classify_history,
    compute_donor_medians,
)


def _monthly(months: int, start: date = date(2024, 1, 1)) -> pd.DataFrame:
    """A monthly frame with a single clear peak in month 1.

    Month 0 ramps up (300), month 1 peaks at 800, then declines. With
    a 12-month peak-detection window, the peak always lands at index
    1. We just vary the total month count to land in different
    history buckets.
    """
    rates = [300.0, 800.0] + [max(50.0, 800.0 * 0.85**i) for i in range(1, months - 1)]
    dates: list[date] = []
    y, m = start.year, start.month
    for _ in range(months):
        dates.append(date(y, m, 1))
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return pd.DataFrame({"prod_date": dates, "rate_calday_bopd": rates})


def test_classify_history_long_when_post_peak_equals_cutoff() -> None:
    # Peak at month idx 1, total 7 months → 6 post-peak months
    # (inclusive of the peak month, per the codebase convention).
    # Cutoff 6 → equal-to-cutoff lands in long.
    df = _monthly(months=7)
    assert classify_history(df, cutoff_months=6) == "long"


def test_classify_history_short_when_one_below_cutoff() -> None:
    # Peak at idx 1, total 6 months → 5 post-peak months → short.
    df = _monthly(months=6)
    assert classify_history(df, cutoff_months=6) == "short"


def test_classify_history_no_peak_on_empty_frame() -> None:
    empty = pd.DataFrame({"prod_date": [], "rate_calday_bopd": []})
    assert classify_history(empty, cutoff_months=6) == "no_peak"


def test_classify_history_long_for_large_history() -> None:
    df = _monthly(months=36)  # 35 post-peak → way above any cutoff
    assert classify_history(df, cutoff_months=6) == "long"


def _fake_forecast(api10: str, di: float | None, b: float | None) -> object:
    """A duck-typed Forecast row — the median helper only reads
    ``api10``, ``di_initial``, and ``b``."""
    return SimpleNamespace(api10=api10, di_initial=di, b=b)


def test_compute_donor_medians_basic() -> None:
    donors = [
        _fake_forecast("A", 0.8, 1.0),
        _fake_forecast("B", 1.2, 1.1),
        _fake_forecast("C", 1.0, 0.95),
    ]
    med = compute_donor_medians(donors)  # type: ignore[arg-type]
    assert isinstance(med, DonorMedians)
    assert med.di == pytest.approx(1.0)
    assert med.b == pytest.approx(1.0)
    assert med.donor_count == 3
    assert set(med.donor_api10s) == {"A", "B", "C"}


def test_compute_donor_medians_skips_partial_donors() -> None:
    # A donor with one of (Di, b) null can't contribute — we drop it
    # rather than imputing the missing value from the other field.
    donors = [
        _fake_forecast("A", 0.8, 1.0),
        _fake_forecast("B", None, 1.1),  # dropped
        _fake_forecast("C", 1.0, None),  # dropped
        _fake_forecast("D", 1.2, 1.0),
    ]
    med = compute_donor_medians(donors)  # type: ignore[arg-type]
    assert med is not None
    assert med.donor_count == 2
    assert set(med.donor_api10s) == {"A", "D"}
    assert med.di == pytest.approx(1.0)
    assert med.b == pytest.approx(1.0)


def test_compute_donor_medians_empty_returns_none() -> None:
    assert compute_donor_medians([]) is None


def test_compute_donor_medians_all_partial_returns_none() -> None:
    donors = [
        _fake_forecast("A", None, 1.0),
        _fake_forecast("B", 0.8, None),
    ]
    assert compute_donor_medians(donors) is None  # type: ignore[arg-type]
