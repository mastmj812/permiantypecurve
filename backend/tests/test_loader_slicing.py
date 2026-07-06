"""Per-stream slice-start logic for the observed TC overlay.

Pins the rule that under peak_month alignment EVERY stream slices from
its own peak (gas commonly peaks after oil; water before), falling back
to the oil peak for streams without a stored peak (rows that pre-date
per-stream peaks). Pure-function tests; the DB-coupled
load_well_series is exercised end-to-end manually.
"""

from __future__ import annotations

from datetime import date

from app.type_curves.loader import (
    _first_index_on_or_after,
    _stream_slice_starts,
)


def test_first_prod_alignment_starts_all_streams_at_first_prod() -> None:
    fp = date(2023, 1, 1)
    starts = _stream_slice_starts(
        alignment="first_prod_month",
        first_prod_date=fp,
        oil_peak_date=date(2023, 4, 1),
        gas_peak_date=date(2023, 6, 1),
        water_peak_date=date(2023, 2, 1),
    )
    assert starts == {"oil": fp, "gas": fp, "water": fp}


def test_peak_month_alignment_each_stream_uses_own_peak() -> None:
    oil_peak = date(2023, 4, 1)
    gas_peak = date(2023, 7, 1)  # rising GOR: gas peaks after oil
    water_peak = date(2023, 2, 1)
    starts = _stream_slice_starts(
        alignment="peak_month",
        first_prod_date=date(2023, 1, 1),
        oil_peak_date=oil_peak,
        gas_peak_date=gas_peak,
        water_peak_date=water_peak,
    )
    assert starts["oil"] == oil_peak
    assert starts["gas"] == gas_peak
    assert starts["water"] == water_peak


def test_peak_month_streams_fall_back_to_oil_peak_when_missing() -> None:
    oil_peak = date(2023, 4, 1)
    starts = _stream_slice_starts(
        alignment="peak_month",
        first_prod_date=date(2023, 1, 1),
        oil_peak_date=oil_peak,
        gas_peak_date=None,
        water_peak_date=None,
    )
    assert starts["gas"] == oil_peak
    assert starts["water"] == oil_peak


def test_first_index_on_or_after() -> None:
    dates = [date(2023, m, 1) for m in range(1, 7)]  # Jan..Jun
    assert _first_index_on_or_after(dates, date(2023, 1, 1)) == 0
    assert _first_index_on_or_after(dates, date(2023, 4, 1)) == 3
    # Target between rows lands on the next row on/after it.
    assert _first_index_on_or_after(dates, date(2023, 3, 15)) == 3
    # None target → offset 0 (no slicing).
    assert _first_index_on_or_after(dates, None) == 0
    # Target after the last row → past-the-end index.
    assert _first_index_on_or_after(dates, date(2024, 1, 1)) == 6
