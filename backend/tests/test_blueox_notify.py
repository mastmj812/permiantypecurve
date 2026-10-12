"""The "Notify Blue Ox" button — #eng-updates drop notice, DB-free.

Pins the message shape of record (codename first; "new drop file:
<file>[, supersedes <prior>]"), the export snapshot (PDP rows never
counted as locations; P50 oil/gas risk multipliers), the zone/count diff
against the last NOTIFIED drop, and the posting gates (webhook set,
filed in the Deal Folder, latest export only, never allow_stale, never
twice).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.auth import get_current_user
from app.core.slack import SlackPostError, post_webhook
from app.db.session import get_session
from app.exports.blueox_notify import (
    block_reason,
    compose_message,
    draft_body,
    summarize_export,
)
from app.main import app

# ---------------- summarize_export ----------------


def _params(oil: float = 1.0, gas: float = 1.0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lv in ("P10", "P50", "P90"):
        rows.append({"stream": "oil", "level": lv, "risk_mult": oil})
        rows.append({"stream": "gas", "level": lv, "risk_mult": gas})
    rows.append({"stream": "water", "level": "P50", "risk_mult": 1.0})
    return rows


def _zone(name: str, cats: list[str], oil: float = 1.0, gas: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        zone_name=name,
        reserve_category="PUD",
        curve_params=_params(oil, gas),
        inventory=[SimpleNamespace(category=c) for c in cats],
    )


def test_summary_counts_pud_upside_only_and_reads_p50_mults() -> None:
    data = SimpleNamespace(
        zones=[
            _zone("WCB_1_N", ["PUD", "PUD", "UPSIDE", "PDP", "PDP"], oil=0.7, gas=0.7),
            _zone("WCC_N", ["UPSIDE"] * 4),
        ],
        risking="geologic_multipliers_applied",
    )
    s = summarize_export(data)  # type: ignore[arg-type]
    assert s["locations"] == 7
    assert s["zones"][0] == {
        "zone": "WCB_1_N",
        "category": "PUD",
        "locations": 3,
        "risk_mult": {"oil": 0.7, "gas": 0.7},
    }
    assert s["zones"][1]["risk_mult"] == {"oil": 1.0, "gas": 1.0}
    assert s["risking"] == "geologic_multipliers_applied"


# ---------------- draft_body ----------------


def _summary(zones: list[tuple[str, int, float, float]]) -> dict[str, Any]:
    return {
        "zones": [
            {"zone": z, "category": "PUD", "locations": n, "risk_mult": {"oil": o, "gas": g}}
            for z, n, o, g in zones
        ],
        "locations": sum(n for _, n, _, _ in zones),
    }


def test_first_drop_has_no_supersedes_or_diff() -> None:
    body = draft_body(
        "rallycaps_curves_2026-10-08.xlsx",
        _summary([("WCB_1_N", 3, 1.0, 1.0), ("WCB_2_N", 4, 1.0, 1.0)]),
    )
    assert body == (
        "new drop file: rallycaps_curves_2026-10-08.xlsx. "
        "2 zones (WCB_1_N, WCB_2_N), 7 PUD/UPSIDE locations. Unrisked."
    )


def test_redrop_names_superseded_file_counts_and_zone_changes() -> None:
    prev = (
        "rallycaps_curves_2026-10-08.xlsx",
        _summary([("WCB_1_N", 3, 1.0, 1.0), ("WCB_2_SE", 12, 1.0, 1.0)]),
    )
    body = draft_body(
        "rallycaps_curves_2026-10-12.xlsx",
        _summary([("WCB_1_N", 3, 0.7, 0.7), ("WCC_N", 4, 1.0, 1.0)]),
        prev,
    )
    assert body == (
        "new drop file: rallycaps_curves_2026-10-12.xlsx, supersedes "
        "rallycaps_curves_2026-10-08.xlsx. 2 zones (WCB_1_N, WCC_N), "
        "7 PUD/UPSIDE locations (was 15). Zones added: WCC_N; dropped: WCB_2_SE. "
        "Risked: WCB_1_N ×0.70 oil/gas; other zones unrisked."
    )


def test_same_day_reexport_does_not_supersede_itself() -> None:
    s = _summary([("WCB_1_N", 3, 1.0, 1.0)])
    body = draft_body("x_curves_2026-10-12.xlsx", s, ("x_curves_2026-10-12.xlsx", s))
    assert "supersedes" not in body
    assert "(was" not in body


def test_split_oil_gas_mults_and_all_risked() -> None:
    body = draft_body("x.xlsx", _summary([("A", 1, 0.8, 0.9)]))
    assert body.endswith("Risked: A ×0.80 oil / ×0.90 gas.")


def test_compose_puts_codename_first() -> None:
    assert compose_message(" RALLY CAPS ", "new drop file: a.xlsx. ") == (
        "RALLY CAPS new drop file: a.xlsx."
    )


# ---------------- block_reason ----------------


def test_block_reason() -> None:
    assert block_reason(allow_stale=False, notified_at=None) is None
    assert "allow_stale" in (block_reason(allow_stale=True, notified_at=None) or "")
    posted = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
    assert "already posted" in (block_reason(allow_stale=False, notified_at=posted) or "")


# ---------------- post_webhook ----------------


def test_post_webhook_ok_and_refused() -> None:
    with patch("app.core.slack.httpx.post", return_value=httpx.Response(200, text="ok")) as p:
        post_webhook("https://hooks.example/x", "hi")
    assert p.call_args.kwargs["json"] == {"text": "hi"}
    with (
        patch("app.core.slack.httpx.post", return_value=httpx.Response(404, text="no_service")),
        pytest.raises(SlackPostError, match="no_service"),
    ):
        post_webhook("https://hooks.example/x", "hi")
    with (
        patch("app.core.slack.httpx.post", side_effect=httpx.ConnectError("boom")),
        pytest.raises(SlackPostError, match="unreachable"),
    ):
        post_webhook("https://hooks.example/x", "hi")


# ---------------- endpoints ----------------

_DEAL_ID = uuid.uuid4()


def _export(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "deal_id": _DEAL_ID,
        "filename": "rallycaps_curves_2026-10-12.xlsx",
        "export_date": date(2026, 10, 12),
        "allow_stale": False,
        "summary": _summary([("WCB_1_N", 3, 0.7, 0.7)]),
        "created_at": datetime(2026, 10, 12, 19, 0, tzinfo=UTC),
        "notified_at": None,
        "notified_codename": None,
        "notified_text": None,
        "notified_by": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def session() -> MagicMock:
    s = MagicMock()
    s.get.return_value = SimpleNamespace(id=_DEAL_ID, name="RallyCaps")
    s.scalars.return_value = []
    return s


@pytest.fixture
def client(session: MagicMock) -> Iterator[TestClient]:
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(email="m@x.com")
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_session, None)


_HOOK = "app.api.deals.settings.slack_eng_updates_webhook_url"


def test_draft_without_exports_is_blocked(client: TestClient) -> None:
    with patch(_HOOK, None):
        r = client.get(f"/api/deals/{_DEAL_ID}/blueox-notify")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["export_id"] is None
    assert "export one first" in body["blocked"]


def test_draft_supersedes_last_notified_and_remembers_codename(
    client: TestClient, session: MagicMock
) -> None:
    latest = _export()
    debug = _export(filename="rallycaps_curves_2026-10-10.xlsx")  # never notified
    shipped = _export(
        filename="rallycaps_curves_2026-10-08.xlsx",
        summary=_summary([("WCB_1_North", 3, 1.0, 1.0)]),
        notified_at=datetime(2026, 10, 8, 19, 0, tzinfo=UTC),
        notified_codename="RALLY CAPS",
    )
    session.scalars.return_value = [latest, debug, shipped]
    with patch(_HOOK, "https://hooks.example/x"):
        body = client.get(f"/api/deals/{_DEAL_ID}/blueox-notify").json()
    assert body["configured"] is True
    assert body["export_id"] == str(latest.id)
    assert body["supersedes"] == "rallycaps_curves_2026-10-08.xlsx"
    assert body["codename"] == "RALLY CAPS"
    assert body["blocked"] is None
    assert "supersedes rallycaps_curves_2026-10-08.xlsx" in body["body"]
    assert "(was" not in body["body"]  # 3 -> 3


def _post(client: TestClient, export_id: uuid.UUID, **kw: Any) -> httpx.Response:
    payload = {
        "export_id": str(export_id),
        "codename": "RALLY CAPS",
        "body": "new drop file: rallycaps_curves_2026-10-12.xlsx.",
        "filed_in_deal_folder": True,
    }
    payload.update(kw)
    r: httpx.Response = client.post(f"/api/deals/{_DEAL_ID}/blueox-notify", json=payload)
    return r


def test_post_gates(client: TestClient, session: MagicMock) -> None:
    latest = _export()
    older = _export(filename="old.xlsx")
    session.scalars.return_value = [latest, older]
    with patch(_HOOK, None):
        assert _post(client, latest.id).status_code == 503
    with patch(_HOOK, "https://hooks.example/x"), patch("app.api.deals.post_webhook") as pw:
        assert _post(client, latest.id, filed_in_deal_folder=False).status_code == 400
        assert _post(client, older.id).status_code == 409  # not the latest
        latest.allow_stale = True
        assert _post(client, latest.id).status_code == 409
    pw.assert_not_called()


def test_post_sends_codename_first_and_stamps_row(client: TestClient, session: MagicMock) -> None:
    latest = _export()
    session.scalars.return_value = [latest]
    with patch(_HOOK, "https://hooks.example/x"), patch("app.api.deals.post_webhook") as pw:
        r = _post(client, latest.id)
        assert r.status_code == 200
        pw.assert_called_once_with(
            "https://hooks.example/x",
            "RALLY CAPS new drop file: rallycaps_curves_2026-10-12.xlsx.",
        )
        assert latest.notified_at is not None
        assert latest.notified_codename == "RALLY CAPS"
        assert latest.notified_by == "m@x.com"
        session.commit.assert_called()
        # second click: already posted
        assert _post(client, latest.id).status_code == 409
    assert pw.call_count == 1


def test_post_surfaces_slack_refusal_as_502(client: TestClient, session: MagicMock) -> None:
    latest = _export()
    session.scalars.return_value = [latest]
    with (
        patch(_HOOK, "https://hooks.example/x"),
        patch(
            "app.api.deals.post_webhook", side_effect=SlackPostError("refused (404): no_service")
        ),
    ):
        r = _post(client, latest.id)
    assert r.status_code == 502
    assert latest.notified_at is None
