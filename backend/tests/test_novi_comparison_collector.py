"""DB-free tests for ``_collect_novi_comparison``'s vintage routing.

After a quarterly Novi Intelligence reload the persisted narvi
``novi_rep`` stick sets reference a SUPERSEDED vintage (Novi renumbers
planned wells every report, so those stick_ids never join the current
curated views). The collector must (a) re-select fresh sticks for the
current-vintage series instead of dropping everything, and (b) plot the
persisted drop-time set against its OWN vintage via the sql/42 by-report
readers. Warehouse calls are monkeypatched — the routing is the unit.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import app.api.deals as deals_mod
from app.api.deals import _collect_novi_comparison
from app.warehouse_client.intel_forecast import (
    IntelMedianSeries,
    IntelReport,
    RepSet,
    resolve_rep_set,
)

_CURRENT_VINTAGE = "2026-09-30"
_PREV_VINTAGE = "2025-09-30"


def _series(scale: float) -> IntelMedianSeries:
    return IntelMedianSeries(
        oil_bbl=(100.0 * scale, 50.0 * scale),
        gas_mcf=(300.0 * scale, 150.0 * scale),
        water_bbl=(200.0 * scale, 100.0 * scale),
        n_sticks=4,
        n_pud=4,
        n_res=0,
        dropped_sticks=(),
    )


def _reports() -> tuple[IntelReport, ...]:
    return (
        IntelReport(
            report_name="basin_research__Delaware_Basin__2025Q3",
            basin_slug="delaware",
            report_version="2025Q3",
            vintage_date=_PREV_VINTAGE,
            is_latest=False,
        ),
        IntelReport(
            report_name="basin_research__Delaware_Basin__2026Q3",
            basin_slug="delaware",
            report_version="2026Q3",
            vintage_date=_CURRENT_VINTAGE,
            is_latest=True,
        ),
    )


def test_stale_persisted_set_routes_to_prev_and_reselects_current(monkeypatch: Any) -> None:
    calls: dict[str, Any] = {}

    def fake_resolve(wh: Any, well: Any, *, ignore_persisted: bool = False) -> RepSet:
        if ignore_persisted:
            calls["reselected"] = True
            return RepSet(
                mode="neighborhood",
                stick_ids=(10, 11, 12),
                low_n=False,
                intel_vintage=None,
                source="fallback",
                lateral_tol=0.25,
            )
        return RepSet(
            mode="neighborhood",
            stick_ids=(1, 2),
            low_n=False,
            intel_vintage=_PREV_VINTAGE,
            source="persisted",
            lateral_tol=0.25,
        )

    def fake_current(wh: Any, ids: tuple[int, ...]) -> IntelMedianSeries:
        calls["current_ids"] = ids
        return _series(1.0)

    def fake_by_report(wh: Any, report: str, ids: tuple[int, ...]) -> IntelMedianSeries:
        calls["prev_report"] = report
        calls["prev_ids"] = ids
        return _series(2.0)

    monkeypatch.setattr(deals_mod, "resolve_rep_set", fake_resolve)
    monkeypatch.setattr(deals_mod, "fetch_intel_median_series", fake_current)
    monkeypatch.setattr(deals_mod, "fetch_intel_median_series_by_report", fake_by_report)
    monkeypatch.setattr(deals_mod, "fetch_available_reports", lambda wh: _reports())

    zone = _collect_novi_comparison(
        wh=None,
        zone_name="WCB_2 West",
        wells=[SimpleNamespace(category="generated")],
        tc_risked=False,
        vintage=_CURRENT_VINTAGE,
    )

    # current series = FRESH selection, not the stale persisted ids
    assert calls["reselected"] is True
    assert calls["current_ids"] == (10, 11, 12)
    assert zone.oil_bbl == _series(1.0).oil_bbl
    assert zone.stale_vintage is True

    # prev series = the persisted drop-time ids against their OWN report
    assert calls["prev_report"] == "basin_research__Delaware_Basin__2025Q3"
    assert calls["prev_ids"] == (1, 2)
    assert zone.prev_intel_vintage == _PREV_VINTAGE
    assert zone.prev_n_sticks == _series(2.0).n_sticks
    assert zone.prev_oil_bbl == _series(2.0).oil_bbl


def test_current_persisted_set_gets_no_prev_overlay(monkeypatch: Any) -> None:
    def fake_resolve(wh: Any, well: Any, *, ignore_persisted: bool = False) -> RepSet:
        assert ignore_persisted is False, "no re-selection when the set is current"
        return RepSet(
            mode="neighborhood",
            stick_ids=(5, 6, 7),
            low_n=False,
            intel_vintage=_CURRENT_VINTAGE,
            source="persisted",
            lateral_tol=0.40,
        )

    monkeypatch.setattr(deals_mod, "resolve_rep_set", fake_resolve)
    monkeypatch.setattr(deals_mod, "fetch_intel_median_series", lambda wh, ids: _series(1.0))
    monkeypatch.setattr(
        deals_mod,
        "fetch_intel_median_series_by_report",
        lambda wh, report, ids: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    monkeypatch.setattr(
        deals_mod,
        "fetch_available_reports",
        lambda wh: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    zone = _collect_novi_comparison(
        wh=None,
        zone_name="BS1_S",
        wells=[SimpleNamespace(category="generated")],
        tc_risked=False,
        vintage=_CURRENT_VINTAGE,
    )
    assert zone.stale_vintage is False
    assert zone.prev_intel_vintage is None
    assert zone.prev_n_sticks == 0
    assert zone.prev_oil_bbl == ()


class _FakeResult:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def scalar(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeWh:
    """Routes the module's SQL constants to canned rows by fragment."""

    def __init__(self, routes: dict[str, list[Any]]):
        self.routes = routes
        self.queries: list[str] = []

    def execute(self, sql: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        stext = str(sql)
        self.queries.append(stext)
        for fragment, rows in self.routes.items():
            if fragment in stext:
                return _FakeResult(rows)
        raise AssertionError(f"unexpected SQL: {stext[:120]}")


def test_self_unresolved_passthrough_degrades_to_neighborhood() -> None:
    """A pud/res pass-through whose Novi stick vanished from the current
    vintage (renamed/renumbered on reload) must fall back to the sql/35
    neighborhood rule around its own geometry — not resolve to None
    (toucan post-2026Q3: every zone's current series was empty)."""
    wh = _FakeWh(
        {
            "unique_id = :uid": [],  # self stick gone from current vintage
            "GROUP BY basin": ["delaware"],  # modal-basin lookup
            "intel_representative_sticks": [(101,), (102,), (103,)],
        }
    )
    well = SimpleNamespace(
        category="pud",
        novi_rep=None,
        novi_wellname="North TX 164 SBSS 1",
        well_name="North TX 164 SBSS 1",
        formation="BS2_S",
        completed_lateral_ft=10000.0,
        legs_lonlat=((-103.2, 31.4, -103.2, 31.42),),
    )
    rep = resolve_rep_set(wh, well)  # type: ignore[arg-type]
    assert rep is not None
    assert rep.mode == "neighborhood"
    assert rep.stick_ids == (101, 102, 103)
    assert rep.source == "fallback"
    assert rep.lateral_tol == 0.25  # delaware

    # Ungeoreferenced legacy pass-through still resolves to None.
    bare = SimpleNamespace(
        category="pud",
        novi_rep=None,
        novi_wellname="North TX 164 SBSS 2",
        well_name="North TX 164 SBSS 2",
        formation=None,
        completed_lateral_ft=None,
        legs_lonlat=(),
    )
    wh2 = _FakeWh({"unique_id = :uid": []})
    assert resolve_rep_set(wh2, bare) is None  # type: ignore[arg-type]
