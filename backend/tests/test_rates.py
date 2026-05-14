"""Rate computation — month-1 partial-month rule is mandatory per the brief."""

from __future__ import annotations

from datetime import date

import pytest

from app.ingest.rates import RateInputs, calendar_days_in, compute_rates


def test_standard_month_uses_calendar_days_for_calday() -> None:
    # Mid-life month: 31-day month, 31 producing days, 31_000 bbl → 1000 BOPD both ways.
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 1, 1),
            first_prod_date=date(2020, 6, 1),
            producing_days=31,
            oil_bbl=31_000.0,
            gas_mcf=62_000.0,
            water_bbl=15_500.0,
        )
    )
    assert r.rate_calday_bopd == pytest.approx(1000.0)
    assert r.rate_prodday_bopd == pytest.approx(1000.0)
    assert r.rate_calday_mcfd == pytest.approx(2000.0)
    assert r.rate_calday_bwpd == pytest.approx(500.0)


def test_partial_producing_days_in_normal_month_only_affects_prodday() -> None:
    """A month past first-prod with low producing_days (downtime / workover):
    calday must keep using calendar days; prodday gets the inflated value."""
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 7, 1),  # July: 31 days
            first_prod_date=date(2020, 6, 1),
            producing_days=10,
            oil_bbl=10_000.0,
            gas_mcf=20_000.0,
            water_bbl=5_000.0,
        )
    )
    assert r.rate_calday_bopd == pytest.approx(10_000.0 / 31)
    assert r.rate_prodday_bopd == pytest.approx(1000.0)


def test_month_one_partial_uses_producing_days_for_calday() -> None:
    """Critical case: first-prod month, partial. calday MUST use producing_days
    or peak-month detection will mis-identify month 2 as peak."""
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 3, 1),  # March, 31 days
            first_prod_date=date(2023, 3, 22),
            producing_days=10,  # spud + first sale on the 22nd
            oil_bbl=10_000.0,
            gas_mcf=20_000.0,
            water_bbl=5_000.0,
        )
    )
    assert r.rate_calday_bopd == pytest.approx(1000.0)  # 10,000 / 10 (PD), not / 31
    assert r.rate_prodday_bopd == pytest.approx(1000.0)
    assert r.rate_calday_mcfd == pytest.approx(2000.0)
    assert r.rate_calday_bwpd == pytest.approx(500.0)


def test_month_one_full_month_uses_calendar_days() -> None:
    """First-prod month where producing_days == calendar days (well started
    on day 1 and ran the full month): calday uses calendar days normally."""
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 4, 1),  # April, 30 days
            first_prod_date=date(2023, 4, 1),
            producing_days=30,
            oil_bbl=30_000.0,
            gas_mcf=None,
            water_bbl=None,
        )
    )
    assert r.rate_calday_bopd == pytest.approx(1000.0)  # 30,000 / 30
    assert r.rate_prodday_bopd == pytest.approx(1000.0)


def test_month_one_producing_days_none_falls_back_to_calendar() -> None:
    """If Enverus doesn't report producing_days for month 1, we can't apply
    the rule. Calday uses calendar days; prodday is null."""
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 4, 1),
            first_prod_date=date(2023, 4, 15),
            producing_days=None,
            oil_bbl=15_000.0,
            gas_mcf=None,
            water_bbl=None,
        )
    )
    # 15000 / 30 = 500
    assert r.rate_calday_bopd == pytest.approx(500.0)
    assert r.rate_prodday_bopd is None


def test_producing_days_zero_yields_none_rates_safely() -> None:
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 4, 1),
            first_prod_date=date(2020, 1, 1),
            producing_days=0,
            oil_bbl=100.0,
            gas_mcf=200.0,
            water_bbl=50.0,
        )
    )
    assert r.rate_prodday_bopd is None
    # calday still computable — calendar days don't depend on producing_days.
    assert r.rate_calday_bopd == pytest.approx(100.0 / 30)


def test_no_first_prod_date_treats_as_normal_month() -> None:
    """If we don't know first-prod yet (header arrived without it), don't
    apply the partial rule — just use calendar days."""
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 3, 1),
            first_prod_date=None,
            producing_days=10,
            oil_bbl=10_000.0,
            gas_mcf=None,
            water_bbl=None,
        )
    )
    assert r.rate_calday_bopd == pytest.approx(10_000.0 / 31)
    assert r.rate_prodday_bopd == pytest.approx(1000.0)


def test_calendar_days_helper() -> None:
    assert calendar_days_in(date(2023, 2, 1)) == 28
    assert calendar_days_in(date(2024, 2, 1)) == 29
    assert calendar_days_in(date(2023, 4, 1)) == 30
    assert calendar_days_in(date(2023, 12, 1)) == 31


def test_null_volumes_yield_null_rates() -> None:
    r = compute_rates(
        RateInputs(
            prod_date=date(2023, 4, 1),
            first_prod_date=date(2020, 1, 1),
            producing_days=30,
            oil_bbl=None,
            gas_mcf=None,
            water_bbl=None,
        )
    )
    assert r.rate_calday_bopd is None and r.rate_prodday_bopd is None
