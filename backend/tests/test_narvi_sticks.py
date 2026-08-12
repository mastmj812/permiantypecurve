"""Narvi deal-sticks overlay — warehouse fetch + endpoint, DB-free.

The main map's dashed planned-stick overlay pulls every non-PDP
inventory well of one narvi deal. These tests pin the PDP exclusion
(persisted handoff_category wins, then category='pdp', then the
pdp_count_3mi>=3 derivation), the raw `_b` formation pass-through, and
the None-vs-[] contract (missing deal -> 404, all-PDP deal -> empty).
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
from app.warehouse_client.narvi import NarviDealStick, fetch_narvi_deal_sticks

# ---------------- fetch_narvi_deal_sticks ----------------


def _row(
    well_name: str,
    *,
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


def _session(exists: bool, rows: list[SimpleNamespace]) -> MagicMock:
    wh = MagicMock()
    probe = MagicMock()
    probe.one_or_none.return_value = (1,) if exists else None
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
    out = fetch_narvi_deal_sticks(_session(True, rows), "vault")
    assert out is not None
    assert [s.well_name for s in out] == ["KEEP 1H", "KEEP 2H"]
    assert [s.category for s in out] == ["PUD", "UPSIDE"]
    assert out[1].formation == "WCA_1_b"  # raw — frontend strips for color only


def test_fetch_missing_deal_returns_none() -> None:
    assert fetch_narvi_deal_sticks(_session(False, []), "typo") is None


def test_fetch_all_pdp_deal_returns_empty_list() -> None:
    rows = [_row("4200011111", category="pdp")]
    out = fetch_narvi_deal_sticks(_session(True, rows), "vault")
    assert out == []


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
    scenario_id="plan_a",
    scenario_name="Plan A",
    well_name="KEEP 1H",
    formation="BS3_C",
    category="PUD",
    well_type="uturn",
    legs_geojson='{"type":"MultiLineString","coordinates":[]}',
    turn_geojson='{"type":"LineString","coordinates":[]}',
)


def test_endpoint_ok(client: TestClient) -> None:
    with (
        patch("app.api.deals.get_warehouse_session", return_value=iter([None])),
        patch("app.api.deals.fetch_narvi_deal_sticks", return_value=[_STICK]),
    ):
        resp = client.get("/api/narvi/deal-sticks", params={"deal_id": "vault"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "vault"
    assert len(body["wells"]) == 1
    w = body["wells"][0]
    assert w["formation"] == "BS3_C"
    assert w["category"] == "PUD"
    assert w["turn_geojson"] == '{"type":"LineString","coordinates":[]}'


def test_endpoint_404_on_missing_deal(client: TestClient) -> None:
    with (
        patch("app.api.deals.get_warehouse_session", return_value=iter([None])),
        patch("app.api.deals.fetch_narvi_deal_sticks", return_value=None),
    ):
        resp = client.get("/api/narvi/deal-sticks", params={"deal_id": "typo"})
    assert resp.status_code == 404


def test_endpoint_502_on_warehouse_error(client: TestClient) -> None:
    with (
        patch("app.api.deals.get_warehouse_session", return_value=iter([None])),
        patch(
            "app.api.deals.fetch_narvi_deal_sticks",
            side_effect=RuntimeError("pooler down"),
        ),
    ):
        resp = client.get("/api/narvi/deal-sticks", params={"deal_id": "vault"})
    assert resp.status_code == 502
