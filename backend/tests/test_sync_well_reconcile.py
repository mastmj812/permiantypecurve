"""Well-header deletion reconcile (2026-10-08).

The header upsert never deleted, so wellbores the warehouse reclassified
out of the sync universe (non-horizontal, permit, dropped) lingered on the
map with stale headers -- 60 rows on 2026-10-08. The reconcile mirrors the
production one, with two guards the production path doesn't need: wells
carrying user-authored / deal-scoped rows are retained (the ``wells`` FKs
cascade), and a shrink beyond 5% of the table is refused as a suspected
warehouse incident. DB-free: the local reads and the delete are stubbed.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.sync.orchestrator as orchestrator
from app.sync.orchestrator import (
    WELL_RECONCILE_MAX_FRACTION,
    _reconcile_well_deletions,
    _well_reconcile_targets,
)

# ------------------------------------------------------------ pure targets


def test_targets_split_stale_into_delete_and_retain() -> None:
    local = ["A", "B", "C", "D"]
    fetched = ["A", "B"]
    protected = ["C", "Z"]  # Z protected but not local -- irrelevant
    assert _well_reconcile_targets(local, fetched, protected) == (["D"], ["C"])


def test_targets_nothing_when_universe_unchanged() -> None:
    assert _well_reconcile_targets(["A", "B"], ["B", "A"], []) == ([], [])


def test_targets_fetched_only_wells_are_not_targets() -> None:
    # A well the warehouse returned that we don't hold yet is the
    # upsert's business, never the reconcile's.
    assert _well_reconcile_targets(["A"], ["A", "NEW"], []) == ([], [])


def test_targets_sorted_for_deterministic_logs() -> None:
    delete, retain = _well_reconcile_targets(["c", "a", "b", "p2", "p1"], [], ["p1", "p2"])
    assert delete == ["a", "b", "c"]
    assert retain == ["p1", "p2"]


# ------------------------------------------------------- orchestration


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    local: list[str],
    protected: set[str],
) -> list[list[str]]:
    """Stub the three DB touch points; return the recorded delete chunks."""
    deletes: list[list[str]] = []
    monkeypatch.setattr(orchestrator, "_local_well_api10s", lambda session: list(local))
    monkeypatch.setattr(orchestrator, "_protected_api10s", lambda session: set(protected))

    def _delete(session: Any, api10s: list[str]) -> int:
        deletes.append(list(api10s))
        return len(api10s)

    monkeypatch.setattr(orchestrator, "_delete_wells", _delete)
    return deletes


def test_reconcile_deletes_stale_unprotected_only(monkeypatch: pytest.MonkeyPatch) -> None:
    local = [f"42{i:08d}" for i in range(100)]
    fetched = local[:95]  # 5 wells left the universe
    protected = {local[95], local[96]}  # two of them have forecasts / TC membership
    deletes = _stub(monkeypatch, local=local, protected=protected)

    deleted, retained = _reconcile_well_deletions(None, fetched)  # type: ignore[arg-type]

    assert (deleted, retained) == (3, 2)
    assert deletes == [[local[97], local[98], local[99]]]


def test_reconcile_skips_when_nothing_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero-row header fetch must never be read as "every well left".
    deletes = _stub(monkeypatch, local=["A", "B"], protected=set())
    assert _reconcile_well_deletions(None, []) == (0, 0)  # type: ignore[arg-type]
    assert deletes == []


def test_reconcile_refuses_shrink_beyond_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    local = [f"42{i:08d}" for i in range(100)]
    n_gone = int(WELL_RECONCILE_MAX_FRACTION * 100) + 1  # one over the cap
    fetched = local[: 100 - n_gone]
    deletes = _stub(monkeypatch, local=local, protected=set())

    deleted, retained = _reconcile_well_deletions(None, fetched)  # type: ignore[arg-type]

    assert (deleted, retained) == (0, 0)
    assert deletes == []


def test_reconcile_at_cap_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    local = [f"42{i:08d}" for i in range(100)]
    n_gone = int(WELL_RECONCILE_MAX_FRACTION * 100)  # exactly at the cap
    fetched = local[: 100 - n_gone]
    deletes = _stub(monkeypatch, local=local, protected=set())

    deleted, _ = _reconcile_well_deletions(None, fetched)  # type: ignore[arg-type]

    assert deleted == n_gone
    assert sum(len(c) for c in deletes) == n_gone


def test_reconcile_chunks_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    # 2,500 stale wells out of 100k (2.5%, under the cap) -> three
    # delete statements of 1000/1000/500, never one giant IN-list.
    local = [f"{i:010d}" for i in range(100_000)]
    fetched = local[2_500:]
    deletes = _stub(monkeypatch, local=local, protected=set())

    deleted, _ = _reconcile_well_deletions(None, fetched)  # type: ignore[arg-type]

    assert deleted == 2_500
    assert [len(c) for c in deletes] == [1000, 1000, 500]
