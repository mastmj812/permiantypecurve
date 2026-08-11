"""Live build-up preview — draft assembly + endpoint, DB-free.

The drawer's waterfall must run through the SAME engine as the export
sheet (compute_buildup); these tests pin the synthetic-provenance
assembly (draft_provenance), the draft dispositions per stage, the
no-AOI degradation, and the endpoint serialization with the spatial
pieces (compute_universe / polygon_area_sq_mi) monkeypatched out.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.type_curves as tc_api
from app.api.auth import get_current_user
from app.db.session import get_session
from app.main import app
from app.type_curves.buildup import STAGE_DESCRIPTIONS
from app.type_curves.live_buildup import compute_draft_buildup, draft_provenance

_POLY = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}


def _uni_well(
    api10: str,
    *,
    first_prod: str | None = "2020-06-01",
    lateral: float | None = 9500.0,
    status: str | None = "PDP",
    spacing: float | None = 660.0,
) -> dict[str, Any]:
    return {
        "api10": api10,
        "name": f"WELL {api10[-2:]}",
        "operator": "OP A",
        "formation": "BONE SPRING 2ND SS",
        "first_prod_date": first_prod,
        "lateral_ft": lateral,
        "status": status,
        "lateral_closer_xy_ft": spacing,
    }


def _universe(wells: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "computed_at": "2026-08-10T00:00:00Z",
        "well_count": len(wells),
        "wells": wells,
    }


_FILTERS = {
    "formations": ["BS2_S"],
    "statuses": ["PDP"],
    "first_prod_start": "2018-01-01",
    "first_prod_end": None,
    "lateral_min_ft": 6000,
    "lateral_max_ft": None,
    "spacing_min_ft": None,
    "spacing_max_ft": None,
    "spacing_include_unbounded": False,
}


def test_draft_provenance_shape() -> None:
    uni = _universe([_uni_well("W01")])
    prov = draft_provenance(
        aoi_polygons=[_POLY],
        formations=["BS2_S", "BS2_C"],
        filter_snapshot=_FILTERS,
        universe=uni,
        manual_exclusions={"W09": {"code": "other", "note": None}},
        polygon_areas_sq_mi=[12.5],
    )
    assert prov["version"] == 2
    assert prov["formations"] == ["BS2_C", "BS2_S"]  # sorted, deduped
    assert prov["aoi"]["polygons"][0]["geometry"] == _POLY
    assert prov["aoi"]["polygons"][0]["area_sq_mi"] == 12.5
    assert prov["selection_events"] == []
    assert prov["partition"] is None
    assert prov["exclusions"] == {}
    assert prov["manual_exclusions"] == {"W09": {"code": "other", "note": None}}
    assert prov["universe"] is uni


def test_draft_dispositions_per_stage() -> None:
    """Filter stages, coded manual exclusion, not_selected, included —
    the pre-forecast waterfall a draft cohort can express."""
    wells = [
        _uni_well("W01"),  # included (in cohort)
        _uni_well("W02", first_prod="2015-01-01"),  # vintage
        _uni_well("W03", lateral=4000.0),  # lateral
        _uni_well("W04", spacing=2800.0),  # spacing sentinel... inert (no bound)
        _uni_well("W05", status="SI"),  # filters_other
        _uni_well("W06"),  # not_selected, coded (manual exclusion)
        _uni_well("W07"),  # not_selected, anonymous
    ]
    prov = draft_provenance(
        aoi_polygons=[_POLY],
        formations=["BS2_S"],
        filter_snapshot=_FILTERS,
        universe=_universe(wells),
        manual_exclusions={
            "W06": {"code": "parent_child_spacing", "note": "standalone parent"}
        },
    )
    b = compute_draft_buildup(cohort_api10s=["W01"], provenance=prov)
    by = {r.api10: r for r in b.rows}
    assert by["W01"].disposition == "included"
    assert by["W02"].disposition == "vintage"
    assert by["W03"].disposition == "lateral"
    # No spacing bound in _FILTERS → the sentinel well passes spacing and,
    # being outside the cohort, lands not_selected.
    assert by["W04"].disposition == "not_selected"
    assert by["W05"].disposition == "filters_other"
    assert by["W06"].disposition == "not_selected"
    assert by["W06"].reason_code == "parent_child_spacing"
    assert by["W07"].disposition == "not_selected"
    assert by["W07"].reason_code is None
    assert b.reconciles


def test_draft_spacing_bound_culls() -> None:
    filters = dict(_FILTERS, spacing_min_ft=400, spacing_max_ft=900)
    wells = [
        _uni_well("W01", spacing=660.0),  # in range → included
        _uni_well("W02", spacing=2800.0),  # sentinel → spacing (toggle off)
        _uni_well("W03", spacing=1200.0),  # out of range → spacing
    ]
    prov = draft_provenance(
        aoi_polygons=[_POLY],
        formations=["BS2_S"],
        filter_snapshot=filters,
        universe=_universe(wells),
        manual_exclusions={},
    )
    b = compute_draft_buildup(cohort_api10s=["W01"], provenance=prov)
    by = {r.api10: r for r in b.rows}
    assert by["W01"].disposition == "included"
    assert by["W02"].disposition == "spacing"
    assert by["W03"].disposition == "spacing"


def test_draft_no_aoi_degrades_to_membership_roster() -> None:
    prov = draft_provenance(
        aoi_polygons=[],
        formations=["BS2_S"],
        filter_snapshot=_FILTERS,
        universe={"computed_at": "2026-08-10T00:00:00Z", "well_count": 0, "wells": []},
        manual_exclusions={},
    )
    b = compute_draft_buildup(cohort_api10s=["W01", "W02"], provenance=prov)
    assert not b.degraded  # provenance exists — this is the no-AOI tier
    assert b.waterfall == []
    assert [r.api10 for r in b.rows] == ["W01", "W02"]
    assert all(r.disposition == "included" for r in b.rows)
    assert any("no AOI recorded" in n for n in b.notes)


# ---------------- endpoint (spatial pieces monkeypatched) ----------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    wells = [
        _uni_well("W01"),
        _uni_well("W02", first_prod="2015-01-01"),
        _uni_well("W03"),
    ]

    def fake_universe(session: Any, polygons: list, formations: list) -> dict[str, Any]:
        if not polygons or not formations:
            return {"computed_at": "2026-08-10T00:00:00Z", "well_count": 0, "wells": []}
        return _universe(wells)

    monkeypatch.setattr(tc_api, "compute_universe", fake_universe)
    monkeypatch.setattr(tc_api, "polygon_area_sq_mi", lambda session, p: 10.0)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "test"}
    app.dependency_overrides[get_session] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)


def test_preview_endpoint_full_flow(client: TestClient) -> None:
    resp = client.post(
        "/api/type-curves/buildup/preview",
        json={
            "aoi_polygons": [_POLY],
            "formations": ["BS2_S"],
            "filter_spec": _FILTERS,
            "cohort_api10s": ["W01", "W03"],
            "manual_exclusions": {},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["no_aoi"] is False
    assert body["universe_count"] == 3
    assert body["aoi_polygon_count"] == 1
    assert body["aoi_total_area_sq_mi"] == 10.0
    by = {r["api10"]: r for r in body["rows"]}
    assert by["W01"]["disposition"] == "included"
    assert by["W02"]["disposition"] == "vintage"
    assert by["W03"]["disposition"] == "included"
    assert body["included_count"] == 2
    assert body["reconciles"] is True
    stages = [w["stage"] for w in body["waterfall"]]
    assert stages == [s for s, _ in STAGE_DESCRIPTIONS if s != "unaccounted"]


def test_preview_endpoint_reason_label(client: TestClient) -> None:
    resp = client.post(
        "/api/type-curves/buildup/preview",
        json={
            "aoi_polygons": [_POLY],
            "formations": ["BS2_S"],
            "filter_spec": _FILTERS,
            "cohort_api10s": ["W01"],
            "manual_exclusions": {
                "W03": {"code": "parent_child_spacing", "note": "parent"}
            },
        },
    )
    assert resp.status_code == 200
    by = {r["api10"]: r for r in resp.json()["rows"]}
    assert by["W03"]["disposition"] == "not_selected"
    assert by["W03"]["reason_code"] == "parent_child_spacing"
    assert by["W03"]["reason_label"] == "Parent-child / spacing"


def test_preview_endpoint_unknown_reason_code_422(client: TestClient) -> None:
    resp = client.post(
        "/api/type-curves/buildup/preview",
        json={
            "aoi_polygons": [_POLY],
            "formations": ["BS2_S"],
            "filter_spec": _FILTERS,
            "cohort_api10s": [],
            "manual_exclusions": {"W03": {"code": "nope", "note": None}},
        },
    )
    assert resp.status_code == 422


def test_preview_endpoint_no_aoi(client: TestClient) -> None:
    resp = client.post(
        "/api/type-curves/buildup/preview",
        json={
            "aoi_polygons": [],
            "formations": ["BS2_S"],
            "filter_spec": _FILTERS,
            "cohort_api10s": ["W01"],
            "manual_exclusions": {},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["no_aoi"] is True
    assert body["universe_count"] == 0
    assert body["waterfall"] == []
    # Roster falls back to membership.
    assert [r["api10"] for r in body["rows"]] == ["W01"]
