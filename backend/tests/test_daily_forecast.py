"""Daily-production PDP engine (app.forecasting.daily) — DB-free."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from scipy.integrate import quad

from app.forecasting import daily
from app.forecasting.models import modified_hyperbolic
from app.forecasting.types import ForecastConfig

TRUE = {"qi": 500.0, "Di": 2.0, "b": 1.05, "Df": 0.08}
FIRST = date(2022, 1, 1)
PEAK_DAY = 40


def _synthetic(
    n_days: int = 1200,
    *,
    noise: float = 0.10,
    seed: int = 7,
    shut_in: tuple[int, int] | None = (900, 920),
    routine_down: int = 12,
    choke: bool = True,
) -> pd.DataFrame:
    """Linear ramp to PEAK_DAY, then TRUE modified hyperbolic; gas = 6x oil,
    water = 8x oil. Multiplicative lognormal noise, a shut-in event, some
    scattered down days, one NaN day and one negative correction day."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(FIRST, periods=n_days, freq="D")
    t = (np.arange(n_days) - PEAK_DAY + 0.5) / daily.DAYS_PER_YEAR_CAL
    arps = modified_hyperbolic(np.maximum(t, 0), **TRUE)
    ramp = TRUE["qi"] * np.clip((np.arange(n_days) + 1) / PEAK_DAY, 0.1, 1.0)
    oil = np.where(np.arange(n_days) < PEAK_DAY, ramp, arps) * rng.lognormal(0, noise, n_days)
    down = np.zeros(n_days, dtype=bool)
    if shut_in:
        down[shut_in[0] : shut_in[1]] = True
    scattered = rng.choice(np.arange(PEAK_DAY + 30, n_days - 30), size=routine_down, replace=False)
    down[scattered] = True
    oil = np.where(down, 0.0, oil)
    df = pd.DataFrame(
        {"prod_date": days, "oil_bbl": oil, "gas_mcf": oil * 6.0, "water_bbl": oil * 8.0}
    )
    if n_days > 301:
        df.loc[300, "oil_bbl"] = np.nan
        df.loc[301, "oil_bbl"] = -12.0
    if choke:
        ch = np.full(n_days, 64.0)
        ch[700:] = 48.0
        ch[1100:] = 0.0  # seller stopped reading the choke: missing, not shut
        df["choke"] = ch
    return df


@pytest.fixture(scope="module")
def prep() -> pd.DataFrame:
    return daily.prepare_daily(_synthetic())


def test_nan_and_negative_days_are_down(prep: pd.DataFrame) -> None:
    assert bool(prep.loc[300, "oil_down"]) and bool(prep.loc[301, "oil_down"])


def test_missing_calendar_days_become_down_rows() -> None:
    df = _synthetic(n_days=200, shut_in=None).drop(index=[150, 151])
    p = daily.prepare_daily(df)
    assert len(p) == 200
    assert p.loc[150:151, "oil_down"].all()


def test_shut_in_event_detected_and_excluded_from_uptime(prep: pd.DataFrame) -> None:
    assert prep.loc[900:919, "event_down"].all()
    assert not prep["event_down"].iloc[:899].any()
    up = daily.compute_uptime(prep)
    assert up.event_days == 20
    # routine down days in the window over the non-event days
    assert up.factor == pytest.approx(1 - up.routine_down_days / (up.calendar_days - 20))
    assert 0.95 < up.factor < 1.0


def test_breaks_shut_in_and_choke_but_choke_zero_is_missing(prep: pd.DataFrame) -> None:
    br = daily.detect_breaks(prep)
    kinds = [(b["kind"], b["start"]) for b in br]
    assert ("shut_in", (FIRST + timedelta(days=900)).isoformat()) in kinds
    chokes = [b for b in br if b["kind"] == "choke_change"]
    assert len(chokes) == 1
    assert (chokes[0]["choke_from"], chokes[0]["choke_to"]) == (64.0, 48.0)


def test_peak_found_near_truth(prep: pd.DataFrame) -> None:
    pk = daily.detect_daily_peak(prep, "oil")
    assert pk is not None
    assert abs((pk.peak_date - (FIRST + timedelta(days=PEAK_DAY))).days) <= 10
    assert pk.peak_rate == pytest.approx(TRUE["qi"], rel=0.12)
    assert daily.classify_stream(prep, "oil", pk) == "fit"


def test_full_history_fit_recovers_parameters(prep: pd.DataFrame) -> None:
    pk = daily.detect_daily_peak(prep, "oil")
    assert pk is not None
    fc = daily.fit_daily_stream(prep, "oil", pk, df_terminal=0.08)
    assert fc.method == "daily_fit"
    assert fc.di == pytest.approx(TRUE["Di"], rel=0.15)
    assert fc.b == pytest.approx(TRUE["b"], abs=0.08)
    assert fc.tail_ratio == pytest.approx(1.0, abs=0.06)
    assert fc.anchor_date == pk.peak_date


def test_window_keeps_time_origin_at_peak(prep: pd.DataFrame) -> None:
    """A late window must give the same (from-peak) Di/b — not a reset
    clock that pins Di at the 0.5 floor."""
    pk = daily.detect_daily_peak(prep, "oil")
    assert pk is not None
    start = pk.peak_date + timedelta(days=500)
    fc = daily.fit_daily_stream(prep, "oil", pk, df_terminal=0.08, fit_start=start)
    assert fc.anchor_date == pk.peak_date
    assert fc.fit_start == start
    assert fc.di > 0.6  # not pinned at the 0.5 floor
    assert fc.tail_ratio == pytest.approx(1.0, abs=0.06)
    # the forward decline both fits imply agree
    full = daily.fit_daily_stream(prep, "oil", pk, df_terminal=0.08)
    assert daily.forward_effective_decline(fc) == pytest.approx(
        daily.forward_effective_decline(full), abs=0.04
    )


def test_unpeaked_when_still_inclining() -> None:
    n = 150
    days = pd.date_range(FIRST, periods=n, freq="D")
    oil = np.linspace(200, 700, n)  # managed choke: rising to the last day
    p = daily.prepare_daily(pd.DataFrame({"prod_date": days, "oil_bbl": oil, "gas_mcf": oil * 5}))
    pk = daily.detect_daily_peak(p, "oil")
    assert daily.classify_stream(p, "oil", pk) == "unpeaked"
    fc = daily.transfer_now(p, "oil", cohort_di=3.4, b=1.0, df_terminal=0.08, donors={"n": 20})
    assert fc.method == "transfer_now"
    assert fc.anchor_date == p["prod_date"].max().date()
    assert fc.qi == pytest.approx(float(np.median(oil[-30:])), rel=1e-6)
    assert daily.forward_effective_decline(fc) == pytest.approx(
        daily.anchor_effective_decline(fc), abs=0.01
    )


def test_no_production_stream() -> None:
    p = daily.prepare_daily(_synthetic(n_days=200, shut_in=None).assign(water_bbl=0.0))
    assert daily.classify_stream(p, "water", daily.detect_daily_peak(p, "water")) == "no_production"


def test_remaining_volume_matches_quadrature_and_monthly_rollup(prep: pd.DataFrame) -> None:
    pk = daily.detect_daily_peak(prep, "oil")
    assert pk is not None
    fc = daily.fit_daily_stream(prep, "oil", pk, df_terminal=0.08)
    uptime, years = 0.93, 50.0
    rem = daily.remaining_volume(fc, uptime, FIRST, years)
    t0 = ((fc.data_through + timedelta(days=1)) - fc.anchor_date).days / daily.DAYS_PER_YEAR_CAL
    t1 = (
        (daily.horizon_end(FIRST, years) + timedelta(days=1)) - fc.anchor_date
    ).days / daily.DAYS_PER_YEAR_CAL
    exact, _ = quad(lambda t: float(modified_hyperbolic(t, **fc.params)), t0, t1, limit=200)
    assert rem == pytest.approx(exact * daily.DAYS_PER_YEAR_CAL * uptime, rel=1e-3)
    vols = daily.forward_daily_volumes(
        fc.params, fc.anchor_date, date(2026, 1, 1), date(2026, 12, 31), uptime
    )
    monthly = daily.monthly_volumes(vols)
    assert len(monthly) == 12
    assert monthly.sum() == pytest.approx(vols.sum())


def test_review_flags() -> None:
    fc = daily.DailyForecast(
        stream="oil",
        method="daily_fit",
        params=dict(TRUE),
        anchor_date=FIRST,
        data_through=date(2026, 7, 3),
        at_bound="b at upper bound (1.2)",
        tail_ratio=1.4,
    )
    br = [{"kind": "shut_in", "start": "2026-01-02"}]
    flags = daily.review_flags(fc, "fit", br, data_through=date(2026, 7, 3))
    assert flags == ["at_bound", "tail_mismatch", "recent_break"]
    assert daily.review_flags(None, "unpeaked", [], data_through=date(2026, 7, 3)) == [
        "unpeaked_transfer"
    ]


def test_b_prior_weight_counts_months_not_daily_points(prep: pd.DataFrame) -> None:
    """~1,100 daily points must not read as 1,100 'months' of history."""
    short = daily.prepare_daily(_synthetic(n_days=PEAK_DAY + 200, shut_in=None, choke=False))
    pk = daily.detect_daily_peak(short, "oil")
    assert pk is not None
    fc = daily.fit_daily_stream(
        short, "oil", pk, df_terminal=0.08, config=ForecastConfig(), b_prior=1.15
    )
    info = fc.diagnostics.get("b_prior")
    assert info is not None and info["weight"] > 0.5
    assert info["n_fit_months"] <= 7


def test_pre_production_days_are_not_an_event_or_uptime_loss() -> None:
    n = 200
    days = pd.date_range(FIRST, periods=n, freq="D")
    oil = np.where(np.arange(n) < 25, 0.0, 400.0)  # report starts 25 d before first sales
    p = daily.prepare_daily(pd.DataFrame({"prod_date": days, "oil_bbl": oil, "gas_mcf": oil * 5}))
    assert p["pre_production"].iloc[:25].all() and not p["pre_production"].iloc[25:].any()
    assert not p["event_down"].any()
    assert daily.compute_uptime(p).factor == pytest.approx(1.0)
    assert [b for b in daily.detect_breaks(p) if b["kind"] == "shut_in"] == []


def test_early_life_choke_changes_recorded_but_not_flagged() -> None:
    n = 400
    days = pd.date_range(FIRST, periods=n, freq="D")
    oil = np.full(n, 500.0)
    choke = np.where(np.arange(n) < 60, 24.0, np.where(np.arange(n) < 300, 40.0, 32.0))
    p = daily.prepare_daily(
        pd.DataFrame({"prod_date": days, "oil_bbl": oil, "gas_mcf": oil * 5, "choke": choke})
    )
    br = daily.detect_breaks(p)
    assert [(b["start"], b["early_life"]) for b in br] == [
        ((FIRST + timedelta(days=60)).isoformat(), True),
        ((FIRST + timedelta(days=300)).isoformat(), False),
    ]
    through = days[-1].date()
    assert daily.review_flags(None, "fit", br[:1], data_through=through) == []
    assert daily.review_flags(None, "fit", br, data_through=through) == ["recent_break"]
