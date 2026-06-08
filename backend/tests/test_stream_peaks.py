"""Per-stream peak selection (``orchestrator.detect_stream_peaks``).

Pins the domain rule that oil and gas share the oil peak while water is
anchored on its own — the fix for water flowback peaking months before
oil ramps to peak. Pure-function tests in the style of test_cohort.py;
the DB-coupled forecast_well is exercised end-to-end manually.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.forecasting.orchestrator import detect_stream_peaks


def _frame(oil: list[float], water: list[float]) -> pd.DataFrame:
    n = len(oil)
    months = [date(2023, (i % 12) + 1, 1) for i in range(n)]
    # gas column present so the frame matches the production schema even
    # though gas inherits the oil peak.
    gas = [o * 2.0 for o in oil]
    return pd.DataFrame(
        {
            "prod_date": months,
            "rate_calday_bopd": oil,
            "rate_calday_mcfd": gas,
            "rate_calday_bwpd": water,
        }
    )


def test_water_gets_own_early_peak_oil_and_gas_share_oil_peak() -> None:
    # Oil ramps to a month-3 peak; water spikes at month 1.
    oil = [200, 450, 700, 900, 820, 700, 600, 520, 460, 410, 370, 340]
    water = [1500, 4200, 2600, 1500, 1100, 900, 780, 700, 640, 600, 560, 530]
    peaks = detect_stream_peaks(_frame(oil, water))

    assert peaks["oil"] is not None and peaks["water"] is not None
    assert peaks["oil"].peak_index == 3
    # Gas inherits the oil peak object exactly.
    assert peaks["gas"] is peaks["oil"]
    # Water is anchored months earlier on its own hard peak.
    assert peaks["water"].peak_index == 1
    assert peaks["water"].peak_rate == 4200
    assert peaks["water"].peak_index < peaks["oil"].peak_index


def test_water_falls_back_to_oil_peak_when_no_water_production() -> None:
    oil = [200, 450, 700, 900, 820, 700, 600, 520]
    water = [0.0] * len(oil)  # no water → no detectable water peak
    peaks = detect_stream_peaks(_frame(oil, water))

    assert peaks["oil"] is not None
    # All-zero water has no real peak; water falls back to the oil peak
    # so the stream still gets an anchor rather than being dropped.
    assert peaks["water"] is peaks["oil"]


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
