"""Per-stream peak selection (``orchestrator.detect_stream_peaks``).

Pins the domain rule that EVERY stream anchors on its own detected
peak — water flowback peaks months before oil, and gas commonly peaks
AFTER oil as the GOR climbs (39% of forecasted wells in this dataset).
A stream with no real production comes back None (the orchestrator
skips it) instead of falling back to the oil anchor, which used to
manufacture phantom forecasts against zeros. Pure-function tests in
the style of test_cohort.py; the DB-coupled forecast_well is exercised
end-to-end manually.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.forecasting.orchestrator import _stream_rate_at_index, detect_stream_peaks
from app.forecasting.peak_detection import detect_onset, detect_peak


def _frame(oil: list[float], water: list[float], gas: list[float] | None = None) -> pd.DataFrame:
    n = len(oil)
    months = [date(2023, (i % 12) + 1, 1) for i in range(n)]
    if gas is None:
        gas = [o * 2.0 for o in oil]
    return pd.DataFrame(
        {
            "prod_date": months,
            "rate_calday_bopd": oil,
            "rate_calday_mcfd": gas,
            "rate_calday_bwpd": water,
        }
    )


def test_each_stream_anchors_on_its_own_peak() -> None:
    # Oil ramps to a month-3 peak; water spikes at month 1; gas (rising
    # GOR) keeps climbing and peaks at month 6 — the Permian pattern
    # that made oil-inherited gas anchors under-read gas qi.
    oil = [200, 450, 700, 900, 820, 700, 600, 520, 460, 410, 370, 340]
    water = [1500, 4200, 2600, 1500, 1100, 900, 780, 700, 640, 600, 560, 530]
    gas = [400, 900, 1500, 2100, 2500, 2800, 3000, 2900, 2750, 2600, 2480, 2370]
    peaks = detect_stream_peaks(_frame(oil, water, gas))

    assert peaks["oil"] is not None and peaks["oil"].peak_index == 3
    # Gas anchors on its OWN later peak, at gas-scale rate.
    assert peaks["gas"] is not None
    assert peaks["gas"].peak_index == 6
    assert peaks["gas"].peak_rate == 3000
    # Water is anchored months earlier on its own hard peak.
    assert peaks["water"] is not None
    assert peaks["water"].peak_index == 1
    assert peaks["water"].peak_rate == 4200
    assert peaks["water"].peak_index < peaks["oil"].peak_index < peaks["gas"].peak_index


def test_stream_with_no_production_returns_none_not_oil_fallback() -> None:
    oil = [200, 450, 700, 900, 820, 700, 600, 520]
    water = [0.0] * len(oil)  # no water → no real water peak
    peaks = detect_stream_peaks(_frame(oil, water))

    assert peaks["oil"] is not None
    # All-zero water has NO peak — the orchestrator skips the stream.
    # The old oil-peak fallback fit zeros against oil-scale qi anchors
    # and manufactured a phantom water forecast.
    assert peaks["water"] is None


def test_onset_transform_matches_motivating_well() -> None:
    # Reproduce the orchestrator's onset anchoring on the real well
    # 4249534197 water series: 5 leading zeros, peak at idx 8 (1569).
    # Expect onset=5, qo=onset rate (746), peak_index_months = 8-5 = 3.
    water = [0, 0, 0, 0, 0, 746, 1231, 1012, 1569, 927, 686, 658]
    oil = [200, 300, 400, 464, 450, 430, 410, 390, 370, 350, 330, 310]
    df = _frame(oil, water)

    wp = detect_peak(df, rate_column="rate_calday_bwpd")
    onset = detect_onset(df, rate_column="rate_calday_bwpd", floor=10.0)
    assert wp is not None and wp.peak_index == 8
    assert onset == 5
    peak_index_months = wp.peak_index - onset
    assert peak_index_months == 3
    assert _stream_rate_at_index(df, onset, "water") == 746
    # No leading zeros on oil → onset 0, ramp anchored at first prod.
    assert detect_onset(df, rate_column="rate_calday_bopd", floor=5.0) == 0


def test_no_oil_peak_yields_none_for_oil_and_gas() -> None:
    empty = pd.DataFrame(
        {
            "prod_date": [],
            "rate_calday_bopd": [],
            "rate_calday_mcfd": [],
            "rate_calday_bwpd": [],
        }
    )
    peaks = detect_stream_peaks(empty)
    assert peaks["oil"] is None
    assert peaks["gas"] is None
    # Water also None (empty frame → fallback target is None too).
    assert peaks["water"] is None
