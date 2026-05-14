"""Peak detection rules.

The big one is the "mid-life spike must not win" rule: a single anomalously
high rate in month 18 (e.g. flush production from an offset frac job)
must not be picked as the well's peak. The brief restricts the search to
the first 12 months for exactly this reason.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.forecasting.peak_detection import detect_oil_peak


def _make(rates: list[float], start: date = date(2023, 1, 1)) -> pd.DataFrame:
    """Build a monthly frame from a rate series; month-by-month from `start`."""
    months: list[date] = []
    y, m = start.year, start.month
    for _ in range(len(rates)):
        months.append(date(y, m, 1))
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    return pd.DataFrame({"prod_date": months, "rate_calday_bopd": rates})


def test_simple_rising_then_falling_picks_max_month() -> None:
    rates = [200, 600, 800, 750, 600, 500, 400, 300]
    df = _make(rates)
    peak = detect_oil_peak(df)
    assert peak is not None
    assert peak.peak_index == 2
    assert peak.peak_month_date == date(2023, 3, 1)
    assert peak.peak_rate == pytest.approx(800)


def test_late_life_spike_does_not_win() -> None:
    # Real peak in month 2; freak spike in month 17 (zero-indexed, so 18th month).
    # If the algorithm didn't restrict to first 12, month 17 would win at 2000.
    rates = [200, 800, 700, 600, 500, 450, 400, 350, 300, 280, 260, 250, 230, 210, 190, 170, 160, 2000, 80]
    df = _make(rates)
    peak = detect_oil_peak(df)
    assert peak is not None
    assert peak.peak_index == 1
    assert peak.peak_rate == 800


def test_short_series_under_12_months_uses_all() -> None:
    rates = [100, 300, 500, 400, 300]
    df = _make(rates)
    peak = detect_oil_peak(df)
    assert peak is not None
    assert peak.peak_index == 2
    assert peak.peak_rate == 500


def test_centered_rolling_max_picks_3mo_plateau_center() -> None:
    # Plateau across months 4..6 — rolling max for those three months all = 900.
    # idxmax returns the FIRST max (month 4).
    rates = [200, 400, 600, 800, 900, 900, 900, 700, 500]
    df = _make(rates)
    peak = detect_oil_peak(df)
    assert peak is not None
    assert peak.peak_rate == 900
    assert peak.peak_index in (4, 5, 6)


def test_empty_frame_returns_none() -> None:
    assert detect_oil_peak(pd.DataFrame()) is None


def test_ramp_up_month_zero_is_not_picked_as_peak() -> None:
    """Real-world scenario: month 0 is a partial start month with very low
    rate (well came online late in the month, Enverus reports
    producing_days=full month anyway). The 3-month centered rolling max
    will tie month 0 against months 1 and 2 because the same high values
    appear in all three windows. Pick the month with the highest ACTUAL
    rate — not the first tied index — so the fitter doesn't get a
    peak_rate of 25 when the real peak is 800.
    """
    rates = [25, 789, 789, 596, 408, 270, 207, 198, 197, 173, 169, 166]
    df = _make(rates)
    peak = detect_oil_peak(df)
    assert peak is not None
    assert peak.peak_rate == pytest.approx(789)
    assert peak.peak_index in (1, 2)  # one of the two genuinely-high months


def test_unsorted_input_is_handled() -> None:
    # Same data as test_simple_rising_then_falling but shuffled.
    rates_in_order = [200, 600, 800, 750, 600, 500, 400, 300]
    df = _make(rates_in_order)
    shuffled = df.sample(frac=1, random_state=42).reset_index(drop=True)
    peak = detect_oil_peak(shuffled)
    assert peak is not None
    assert peak.peak_month_date == date(2023, 3, 1)
    assert peak.peak_rate == 800
