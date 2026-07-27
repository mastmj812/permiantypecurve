"""Unit tests for the median novi_intel forecast client (no DB).

A stub Session routes each SQL statement to canned rows by fragment
matching, so the median math, the Arps tail splice (erebor's stitch
ported), per-1,000-ft normalization, and the rep-set resolution rules
are all pinned without a warehouse.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from app.warehouse_client.intel_forecast import (
    N_MONTHS,
    REP_LOW_N,
    STEP_DAYS,
    RepSet,
    _tail_rates,
    fetch_intel_median_series,
    resolve_rep_set,
)
from app.warehouse_client.narvi import NarviInventoryWell


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def mappings(self) -> _Result:
        return self

    def scalar(self) -> Any:
        return self._rows[0][0] if self._rows else None


class _StubSession:
    """execute() routes on a distinctive fragment of each module SQL."""

    def __init__(self, routes: dict[str, list[Any]]):
        self.routes = routes
        self.calls: list[tuple[str, Any]] = []

    def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, params))
        for frag, rows in self.routes.items():
            if frag in sql:
                return _Result(rows)
        raise AssertionError(f"unrouted SQL: {sql[:120]}")


def _well(**kw: Any) -> NarviInventoryWell:
    base: dict[str, Any] = {
        "deal_id": "d", "scenario_id": "s", "well_name": "w", "formation": "BS1_S",
        "completed_lateral_ft": 10_000.0, "drilled_lateral_ft": 10_000.0,
        "well_type": "single", "category": "generated",
        "legs_lonlat": ((-103.8, 31.9, -103.77, 31.9),),
    }
    return NarviInventoryWell(**{**base, **kw})


# ============================ rep-set resolution ============================


def test_persisted_novi_rep_wins_no_db_roundtrip() -> None:
    wh = _StubSession({})  # any execute would raise
    rep = resolve_rep_set(wh, _well(novi_rep={
        "mode": "neighborhood", "stick_ids": [3, 1, 2], "n": 3,
        "low_n": False, "intel_vintage": "2025-09-30",
    }))
    assert rep == RepSet(
        mode="neighborhood", stick_ids=(3, 1, 2), low_n=False,
        intel_vintage="2025-09-30", source="persisted",
    )
    assert wh.calls == []


def test_pdp_and_context_none() -> None:
    wh = _StubSession({})
    assert resolve_rep_set(wh, _well(category="pdp")) is None


def test_fallback_self_resolves_stick_by_unique_id() -> None:
    wh = _StubSession({"unique_id = :uid": [(4242,)]})
    rep = resolve_rep_set(
        wh, _well(category="pud", novi_wellname="BLUE OX 23-14 1H"))
    assert rep is not None
    assert rep.mode == "self" and rep.stick_ids == (4242,)
    assert rep.source == "fallback" and rep.low_n is False


def test_fallback_neighborhood_flags_low_n_never_widens() -> None:
    wh = _StubSession({"intel_representative_sticks": [(9,), (10,)]})
    rep = resolve_rep_set(wh, _well())
    assert rep is not None
    assert rep.mode == "neighborhood" and rep.stick_ids == (9, 10)
    assert rep.low_n is True and len(rep.stick_ids) < REP_LOW_N
    rep_calls = [c for c in wh.calls if "intel_representative_sticks" in c[0]]
    assert len(rep_calls) == 1  # one shot; no second, wider attempt
    # the wine-rack bimodal suffix strips before hitting the warehouse
    wh2 = _StubSession({"intel_representative_sticks": []})
    resolve_rep_set(wh2, _well(formation="WCA_1_b"))
    assert wh2.calls[0][1]["bench"] == "WCA_1"


# ============================ tail evaluation ============================


def test_tail_rates_matches_arps_forms() -> None:
    seg_h = {
        "segment_curve_type": "hyperbolic", "b": 1.1, "d_nom": 2.5,
        "q_start": 500.0, "day_start": 0.0, "day_stop": 36_500.0,
    }
    seg_e = {
        "segment_curve_type": "exponential", "b": 0.0, "d_nom": 0.08,
        "q_start": 40.0, "day_start": 3_650.0, "day_stop": 36_500.0,
    }
    days = [365.0]
    (rate_h,) = _tail_rates([seg_h], days)
    assert rate_h == pytest.approx(500.0 * (1 + 1.1 * 2.5 * 1.0) ** (-1 / 1.1))
    (rate_e,) = _tail_rates([seg_e], [3_650.0 + 365.0])
    assert rate_e == pytest.approx(40.0 * math.exp(-0.08))
    # a day covered by no segment stays 0
    assert _tail_rates([seg_e], [100.0]) == [0.0]


# ============================ median series ============================


def _routes_for_two_sticks() -> dict[str, list[Any]]:
    """Stick 1 (ll 10,000 ft) rates 100/300/200; stick 2 (ll 5,000 ft)
    rates 80/240/160 — normalized per-1000ft: s1 10/30/20, s2 16/48/32.
    Median of two = mean. Stick 2 also carries an oil Arps segment for
    the tail beyond its 2 forecast periods."""
    return {
        "FROM curated.intel_locations il": [
            (1, "PW A", "PUD", 10_000.0, "delaware"),
            (2, "PW B", "RES", 5_000.0, "delaware"),
        ],
        "FROM curated.intel_forecast": [
            ("PW A", 1, 100.0, 300.0, 200.0),
            ("PW A", 2, 90.0, 270.0, 180.0),
            ("PW B", 1, 80.0, 240.0, 160.0),
            ("PW B", 2, 72.0, 216.0, 144.0),
        ],
        "FROM curated.intel_arps": [],
    }


def test_median_series_normalizes_before_median() -> None:
    wh = _StubSession(_routes_for_two_sticks())
    s = fetch_intel_median_series(wh, (1, 2))
    assert s is not None
    assert s.n_sticks == 2 and s.n_pud == 1 and s.n_res == 1
    assert len(s.oil_bbl) == N_MONTHS
    # month 1: median(100/10, 80/5) = median(10, 16) = 13 per-1000ft/day
    assert s.oil_bbl[0] == pytest.approx(13.0 * STEP_DAYS)
    assert s.gas_mcf[0] == pytest.approx(39.0 * STEP_DAYS)
    assert s.water_bbl[0] == pytest.approx(26.0 * STEP_DAYS)
    # beyond the forecast with no Arps: zero, never extrapolated silently
    assert s.oil_bbl[10] == 0.0


def test_arps_tail_splices_after_last_forecast_period() -> None:
    routes = _routes_for_two_sticks()
    routes["FROM curated.intel_arps"] = [
        {
            "novi_wellname": "PW B", "production_stream": "oil",
            "segment_curve_type": "exponential", "b": 0.0, "d_nom": 0.05,
            "q_start": 70.0, "day_start": 60.0, "day_stop": 40_000.0,
        },
    ]
    wh = _StubSession(routes)
    s = fetch_intel_median_series(wh, (1, 2))
    assert s is not None
    # month 3 (day 90): stick 1 has no tail (0), stick 2 tail rate =
    # 70*exp(-0.05*(90-60)/365) per day / 5 per-1000ft.
    expected_b = 70.0 * math.exp(-0.05 * 30.0 / 365.0) / 5.0
    assert s.oil_bbl[2] == pytest.approx(
        (0.0 + expected_b) / 2.0 * STEP_DAYS, rel=1e-9)


def test_sticks_without_lateral_or_series_drop_loudly() -> None:
    routes = _routes_for_two_sticks()
    routes["FROM curated.intel_locations il"] = [
        (1, "PW A", "PUD", 10_000.0, "delaware"),
        (2, "PW B", "RES", None, "delaware"),      # no lateral
        (3, "PW C", "PUD", 8_000.0, "delaware"),   # no forecast/arps rows
    ]
    wh = _StubSession(routes)
    s = fetch_intel_median_series(wh, (1, 2, 3, 4))  # 4 = not in vintage
    assert s is not None
    assert s.n_sticks == 1
    assert set(s.dropped_sticks) == {2, 3, 4}
    # median of one = stick 1 alone
    assert s.oil_bbl[0] == pytest.approx(10.0 * STEP_DAYS)


def test_empty_ids_none() -> None:
    assert fetch_intel_median_series(_StubSession({}), ()) is None
