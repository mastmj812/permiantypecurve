"""Decision rule for the production deletion reconcile.

The sync's production phase upserts and never deletes, so months the
vendor retracts would linger locally. ``_production_reconcile_targets``
picks the wells whose local month count disagrees with what the
warehouse just returned — those (and only those) get their stale rows
deleted. These tests pin the rule; the DB delete itself is a thin
anti-join exercised by the live sync.
"""

from app.sync.orchestrator import _production_reconcile_targets


def test_no_targets_when_counts_agree() -> None:
    local = {"4200000001": 24, "4200000002": 36}
    fetched = {"4200000001": 24, "4200000002": 36}
    assert _production_reconcile_targets(local, fetched) == []


def test_local_extra_months_flagged() -> None:
    # Vendor retracted 189 of 195 months (the 2024-08 pad-spike family).
    local = {"4200000001": 195, "4200000002": 36}
    fetched = {"4200000001": 6, "4200000002": 36}
    assert _production_reconcile_targets(local, fetched) == ["4200000001"]


def test_well_absent_from_warehouse_flagged() -> None:
    # Zero warehouse rows -> whole local history is retracted.
    local = {"4200000001": 12}
    fetched = {}
    assert _production_reconcile_targets(local, fetched) == ["4200000001"]


def test_fetched_only_well_is_not_a_target() -> None:
    # A well the warehouse returned but local GROUP BY missed can't
    # happen post-upsert, but the rule must not invent targets from it.
    local = {}
    fetched = {"4200000001": 12}
    assert _production_reconcile_targets(local, fetched) == []


def test_targets_sorted_for_deterministic_logs() -> None:
    local = {"4200000009": 5, "4200000001": 5}
    fetched = {}
    assert _production_reconcile_targets(local, fetched) == [
        "4200000001",
        "4200000009",
    ]
