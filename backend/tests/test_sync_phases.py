"""Sync hardening (2026-10-07): chunked warehouse fetches that never hold
a warehouse transaction open across the local write loop, and per-phase
isolation so one failing phase doesn't skip the rest.

Background: three consecutive nightly runs failed with ``SSL error:
unexpected eof`` because the laptop entered Modern Standby mid-run; the
dead socket only surfaced at the END of a phase (reconcile re-fetch or
Session close) after every row had already landed, and the exception
aborted the later phases. DB-free: the warehouse Session and the phase
functions are stubbed.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

import app.sync.orchestrator as orchestrator
from app.sync.orchestrator import SyncPhaseError, _fetch_in_chunks, sync_permian

# ------------------------------------------------------------ chunked fetch


class _FakeSession:
    """Stands in for ``sqlalchemy.orm.Session(engine)`` as a context
    manager; records its lifecycle so the test can prove the session is
    closed before the chunk is handed to the caller."""

    instances: ClassVar[list[_FakeSession]] = []

    def __init__(self, engine: object) -> None:
        self.engine = engine
        self.open = False
        self.closed = False
        _FakeSession.instances.append(self)

    def __enter__(self) -> _FakeSession:
        self.open = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.open = False
        self.closed = True


def test_fetch_in_chunks_closes_session_before_yielding(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSession.instances = []
    monkeypatch.setattr(orchestrator, "Session", _FakeSession)
    engine = object()

    def fake_fetch(wh: Any, chunk: list[str]) -> Iterator[tuple[str, int]]:
        assert isinstance(wh, _FakeSession) and wh.open, "fetch must run inside the open session"
        assert wh.engine is engine
        for a in chunk:
            yield (a, 1)
            yield (a, 2)

    api10s = [f"42{i:08d}" for i in range(7)]
    seen: list[tuple[list[str], list[tuple[str, int]]]] = []
    for chunk, records in _fetch_in_chunks(engine, fake_fetch, api10s, chunk_wells=3):  # type: ignore[arg-type]
        # By the time the caller holds the records, the warehouse session
        # that produced them is already closed -- the local write loop
        # never overlaps an open warehouse transaction.
        assert _FakeSession.instances[-1].closed
        seen.append((chunk, records))

    assert [c for c, _ in seen] == [api10s[0:3], api10s[3:6], api10s[6:7]]
    assert [len(r) for _, r in seen] == [6, 6, 2]
    assert len(_FakeSession.instances) == 3  # one short-lived session per chunk
    assert all(s.closed for s in _FakeSession.instances)


def test_fetch_in_chunks_empty_universe_opens_no_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSession.instances = []
    monkeypatch.setattr(orchestrator, "Session", _FakeSession)
    assert list(_fetch_in_chunks(object(), lambda wh, c: [], [])) == []  # type: ignore[arg-type]
    assert _FakeSession.instances == []


# --------------------------------------------------------- phase isolation


def _stub_phases(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[str]:
    """Replace the three phase functions with recorders that write a
    recognizable count (or raise when named in ``fail``)."""
    calls: list[str] = []
    monkeypatch.setattr(orchestrator, "_warehouse_engine", lambda: object())

    def _headers(engine: Any, counts: dict[str, int], fca: Any, ho: Any) -> None:
        calls.append("headers")
        if "headers" in fail:
            raise RuntimeError("headers boom")
        counts["headers"] = 11

    def _production(engine: Any, counts: dict[str, int]) -> None:
        calls.append("production")
        if "production" in fail:
            raise ConnectionError("SSL error: unexpected eof while reading")
        counts["production"] = 22
        counts["production_deleted"] = 2

    def _novi(engine: Any, counts: dict[str, int]) -> None:
        calls.append("novi_forecast")
        if "novi_forecast" in fail:
            raise RuntimeError("novi boom")
        counts["novi_forecast"] = 33

    monkeypatch.setattr(orchestrator, "_phase_headers", _headers)
    monkeypatch.setattr(orchestrator, "_phase_production", _production)
    monkeypatch.setattr(orchestrator, "_phase_novi_forecast", _novi)
    return calls


def test_all_phases_succeed_returns_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_phases(monkeypatch)
    counts = sync_permian()
    assert calls == ["headers", "production", "novi_forecast"]
    assert counts == {
        "headers": 11,
        "wells_deleted": 0,
        "wells_retained": 0,
        "production": 22,
        "production_deleted": 2,
        "novi_forecast": 33,
    }


def test_headers_only_when_pull_production_false(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_phases(monkeypatch)
    counts = sync_permian(pull_production=False)
    assert calls == ["headers"]
    assert counts["headers"] == 11 and counts["production"] == 0


def test_failed_phase_does_not_skip_later_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_phases(monkeypatch, fail={"production"})
    with pytest.raises(SyncPhaseError) as exc:
        sync_permian()
    # The Novi forecast phase ran even though production failed -- the
    # 2026-10-07 night lost it for no reason.
    assert calls == ["headers", "production", "novi_forecast"]
    err = exc.value
    assert set(err.failures) == {"production"}
    assert "unexpected eof" in err.failures["production"]
    # Counts from the phases that completed ride along for the log line.
    assert err.counts["headers"] == 11
    assert err.counts["novi_forecast"] == 33
    assert err.counts["production"] == 0
    assert "production" in str(err) and "'headers': 11" in str(err)


def test_every_phase_failing_names_them_all(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_phases(monkeypatch, fail={"headers", "production", "novi_forecast"})
    with pytest.raises(SyncPhaseError) as exc:
        sync_permian()
    assert calls == ["headers", "production", "novi_forecast"]
    assert sorted(exc.value.failures) == ["headers", "novi_forecast", "production"]
