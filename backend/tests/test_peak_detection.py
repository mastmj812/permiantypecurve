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

from app.forecasting.peak_detection import (
    detect_oil_peak,
    detect_onset,
    detect_peak,
    onset_index_from_rates,
)


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


def test_detect_peak_on_water_column_catches_early_hard_peak() -> None:
    """Permian flowback: water peaks hard in month 0-1 and declines fast,
    while oil ramps to its peak around month 3-4. Detecting on the water
    column must land the water peak early, not inherit oil's later peak.
    """
    months = [date(2023, i + 1, 1) for i in range(12)]
    # Oil ramps to a month-3 peak; water spikes at month 1 then craters.
    oil = [200, 450, 700, 900, 820, 700, 600, 520, 460, 410, 370, 340]
    water = [1500, 4200, 2600, 1500, 1100, 900, 780, 700, 640, 600, 560, 530]
    df = pd.DataFrame(
        {"prod_date": months, "rate_calday_bopd": oil, "rate_calday_bwpd": water}
    )

    oil_peak = detect_peak(df, rate_column="rate_calday_bopd")
    water_peak = detect_peak(df, rate_column="rate_calday_bwpd")
    assert oil_peak is not None and water_peak is not None
    # Oil peaks at index 3 (900); water at index 1 (4200) — months apart.
    assert oil_peak.peak_index == 3
    assert water_peak.peak_index == 1
    assert water_peak.peak_rate == pytest.approx(4200)


def test_detect_peak_water_peaking_at_month_zero() -> None:
    """Water that peaks in the very first month (no ramp-up): index 0 must
    win on the water column since it carries the highest actual rate."""
    months = [date(2023, 1 + i, 1) for i in range(8)]
    water = [5000, 3000, 2000, 1400, 1100, 900, 800, 720]
    df = pd.DataFrame({"prod_date": months, "rate_calday_bwpd": water})
    peak = detect_peak(df, rate_column="rate_calday_bwpd")
    assert peak is not None
    assert peak.peak_index == 0
    assert peak.peak_rate == pytest.approx(5000)


def test_detect_oil_peak_is_detect_peak_with_oil_default() -> None:
    rates = [200, 600, 800, 750, 600, 500, 400, 300]
    df = _make(rates)
    assert detect_oil_peak(df) == detect_peak(df, rate_column="rate_calday_bopd")


def test_onset_trims_leading_zero_months() -> None:
    # Real motivating well 4249534197: 5 leading zero water months, then
    # water starts at idx 5 and is sustained. Onset must be idx 5.
    rates = [0, 0, 0, 0, 0, 746, 1231, 1012, 1569, 927]
    assert onset_index_from_rates(rates, floor=10.0) == 5


def test_onset_zero_when_producing_from_month_zero() -> None:
    rates = [800, 600, 500, 400, 300]
    assert onset_index_from_rates(rates, floor=10.0) == 0


def test_onset_skips_lone_early_blip() -> None:
    # A single spurious 50 at idx 1, surrounded by zeros, must NOT anchor
    # onset — real sustained production starts at idx 4.
    rates = [0, 50, 0, 0, 600, 700, 650]
    assert onset_index_from_rates(rates, floor=10.0) == 4


def test_onset_all_zero_returns_zero() -> None:
    assert onset_index_from_rates([0, 0, 0, 0], floor=10.0) == 0


def test_onset_respects_floor() -> None:
    # Sub-floor (< 10) leading months are trimmed even when non-zero.
    rates = [3, 5, 8, 200, 400, 380]
    assert onset_index_from_rates(rates, floor=10.0) == 3


def test_onset_last_month_no_lookahead_accepts() -> None:
    # Production only in the final month: no look-ahead, accept outright.
    rates = [0, 0, 0, 500]
    assert onset_index_from_rates(rates, floor=10.0) == 3


def test_detect_onset_dataframe_wrapper() -> None:
    months = [date(2023, i + 1, 1) for i in range(8)]
    water = [0, 0, 0, 0, 0, 746, 1231, 1012]
    df = pd.DataFrame({"prod_date": months, "rate_calday_bwpd": water})
    assert detect_onset(df, rate_column="rate_calday_bwpd", floor=10.0) == 5


def test_unsorted_input_is_handled() -> None:
    # Same data as test_simple_rising_then_falling but shuffled.
    rates_in_order = [200, 600, 800, 750, 600, 500, 400, 300]
    df = _make(rates_in_order)
    shuffled = df.sample(frac=1, random_state=42).reset_index(drop=True)
    peak = detect_oil_peak(shuffled)
    assert peak is not None
    assert peak.peak_month_date == date(2023, 3, 1)
    assert peak.peak_rate == 800
