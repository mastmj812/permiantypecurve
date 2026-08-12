"""Narvi deal-sticks overlay — warehouse fetch + endpoint, DB-free.

The main map's dashed planned-stick overlay pulls every non-PDP
inventory well of a SET of narvi deals (deal_ids are per-DSU, so one
engineer-named deal like "vault" spans many of them). These tests pin
the PDP exclusion (persisted handoff_category wins, then
category='pdp', then the pdp_count_3mi>=3 derivation), the raw `_b`
formation pass-through, and the found/missing contract (no id found ->
404, partial misses reported, all-PDP deals contribute no sticks).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_current_user
from app.db.session import get_session
from app.main import app
from app.warehouse_client.narvi import (
    NarviDealStick,
    NarviDealSticksResult,
    fetch_narvi_deal_sticks,
)

# ---------------- fetch_narvi_deal_sticks ----------------


def _row(
    well_name: str,
    *,
    deal_id: str = "vault_dsu_1_11",
    formation: str | None = "WCA_1",
    category: str | None = "generated",
    pdp_count_3mi: int | None = None,
    handoff_category: str | None = None,
    scenario_id: str = "plan_a",
    scenario_name: str | None = "Plan A",
    well_type: str = "single",
    legs: str | None = '{"type":"MultiLineString","coordinates":[]}',
    turn: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        deal_id=deal_id,
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        well_name=well_name,
        formation=formation,
        well_type=well_type,
        legs_geojson=legs,
        turn_geojson=turn,
        category=category,
        pdp_count_3mi=pdp_count_3mi,
        handoff_category=handoff_category,
    )


def _session(found_deal_ids: list[str], rows: list[SimpleNamespace]) -> MagicMock:
    wh = MagicMock()
    probe = MagicMock()
    probe.all.return_value = [SimpleNamespace(deal_id=d) for d in found_deal_ids]
    sticks = MagicMock()
    sticks.all.return_value = rows
    wh.execute.side_effect = [probe, sticks]
    return wh


def test_fetch_excludes_pdp_and_keeps_raw_formation() -> None:
    rows = [
        # existing producer by provenance -> PDP -> excluded
        _row("4200011111", category="pdp"),
        # persisted handoff_category WINS over a non-pdp provenance
        _row("OVERRIDE 1H", category="generated", handoff_category="PDP"),
        # >=3 offsets -> PUD
        _row("KEEP 1H", pdp_count_3mi=5),
        # unscored -> UPSIDE; raw _b suffix passes through untouched
        _row("KEEP 2H", formation="WCA_1_b", pdp_count_3mi=None),
    ]
    out = fetch_narvi_deal_sticks(_session(["vault_dsu_1_11"], rows), ["vault_dsu_1_11"])
    assert out.found_deal_ids == ("vault_dsu_1_11",)
    assert out.missing_deal_ids == ()
    assert [s.well_name for s in out.sticks] == ["KEEP 1H", "KEEP 2H"]
    assert [s.category for s in out.sticks] == ["PUD", "UPSIDE"]
    assert out.sticks[1].formation == "WCA_1_b"  # raw — frontend strips for color only


def test_fetch_multi_deal_reports_partial_misses() -> None:
    rows = [
        _row("A 1H", deal_id="vault_dsu_1_11"),
        _row("B 1H", deal_id="vault_dsu_14_23", scenario_id="plan_b"),
    ]
    out = fetch_narvi_deal_sticks(
        _session(["vault_dsu_1_11", "vault_dsu_14_23"], rows),
        ["vault_dsu_1_11", "typo_dsu", "vault_dsu_14_23"],
    )
    assert out.found_deal_ids == ("vault_dsu_1_11", "vault_dsu_14_23")
    assert out.missing_deal_ids == ("typo_dsu",)
    assert [s.deal_id for s in out.sticks] == ["vault_dsu_1_11", "vault_dsu_14_23"]


def test_fetch_no_deal_found_skips_sticks_query() -> None:
    wh = MagicMock()
    probe = MagicMock()
    probe.all.return_value = []
    wh.execute.side_effect = [probe]
    out = fetch_narvi_deal_sticks(wh, ["typo"])
    assert out.found_deal_ids == ()
    assert out.missing_deal_ids == ("typo",)
    assert out.sticks == ()
    assert wh.execute.call_count == 1  # never ran the sticks query


def test_fetch_empty_selection_short_circuits() -> None:
    wh = MagicMock()
    out = fetch_narvi_deal_sticks(wh, [])
    assert out == NarviDealSticksResult((), (), ())
    wh.execute.assert_not_called()


def test_fetch_all_pdp_deal_contributes_no_sticks() -> None:
    rows = [_row("4200011111", category="pdp")]
    out = fetch_narvi_deal_sticks(_session(["vault_dsu_1_11"], rows), ["vault_dsu_1_11"])
    assert out.found_deal_ids == ("vault_dsu_1_11",)
    assert out.sticks == ()


# ---------------- endpoint ----------------


@pytest.fixture()
def client() -> Any:
    app.dependency_overrides[get_current_user] = lambda: {"sub": "test"}
    app.dependency_overrides[get_session] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)


_STICK = NarviDealStick(
    deal_id="vault_dsu_1_11",
    scenario_id="plan_a",
    scenario_name="Plan A",
    well_name="KEEP 1H",
    formation="BS3_C",
    category="PUD",
    well_type="uturn",
    legs_geojson='{"type":"MultiLineString","coordinates":[]}',
    turn_geojson='{"type":"LineString","coordinates":[]}',
)


def test_endpoint_ok_with_partial_miss(client: TestClient) -> None:
    result = NarviDealSticksResult(
        found_deal_ids=("vault_dsu_1_11",),
        missing_deal_ids=("typo_dsu",),
        sticks=(_STICK,),
    )
    with (
        patch("app.api.deals.get_warehouse_session", return_value=iter([None])),
        patch("app.api.deals.fetch_narvi_deal_sticks", return_value=result),
    ):
        resp = client.get(
            "/api/narvi/deal-sticks",
            params=[("deal_id", "vault_dsu_1_11"), ("deal_id", "typo_dsu")],
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_ids"] == ["vault_dsu_1_11"]
    assert body["missing_deal_ids"] == ["typo_dsu"]
    assert len(body["wells"]) == 1
    w = body["wells"][0]
    assert w["deal_id"] == "vault_dsu_1_11"
    assert w["formation"] == "BS3_C"
    assert w["category"] == "PUD"
    assert w["turn_geojson"] == '{"type":"LineString","coordinates":[]}'


def test_endpoint_404_when_nothing_found(client: TestClient) -> None:
    with (
        patch("app.api.deals.get_warehouse_session", return_value=iter([None])),
        patch(
            "app.api.deals.fetch_narvi_deal_sticks",
            return_value=NarviDealSticksResult((), ("typo",), ()),
        ),
    ):
        resp = client.get("/api/narvi/deal-sticks", params={"deal_id": "typo"})
    assert resp.status_code == 404


def test_endpoint_422_without_deal_id(client: TestClient) -> None:
    resp = client.get("/api/narvi/deal-sticks")
    assert resp.status_code == 422


def test_endpoint_502_on_warehouse_error(client: TestClient) -> None:
    with (
        patch("app.api.deals.get_warehouse_session", return_value=iter([None])),
        patch(
            "app.api.deals.fetch_narvi_deal_sticks",
            side_effect=RuntimeError("pooler down"),
        ),
    ):
        resp = client.get("/api/narvi/deal-sticks", params={"deal_id": "vault_dsu_1_11"})
    assert resp.status_code == 502
