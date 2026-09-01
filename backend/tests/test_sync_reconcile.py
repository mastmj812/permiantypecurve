"""Decision rule for the production deletion reconcile.

The sync's production phase upserts and never deletes, so months the
vendor retracts would linger locally. ``_production_reconcile_targets``
picks the wells whose local month count disagrees with what the
warehouse just returned — those (and only those) get their stale rows
deleted. These tests pin the rule; the DB delete itself is a thin
anti-join exercised by the live sync.
"""

from datetime import date
from typing import cast

import pytest
from sqlalchemy import Table, create_engine, select
from sqlalchemy.orm import Session

import app.sync.orchestrator as orchestrator
from app.db.models import ProductionMonthly
from app.sync.orchestrator import (
    _production_reconcile_targets,
    _reconcile_production_deletions,
)
from app.warehouse_client.base import ProductionRecord


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
    fetched: dict[str, int] = {}
    assert _production_reconcile_targets(local, fetched) == ["4200000001"]


def test_fetched_only_well_is_not_a_target() -> None:
    # A well the warehouse returned but local GROUP BY missed can't
    # happen post-upsert, but the rule must not invent targets from it.
    local: dict[str, int] = {}
    fetched = {"4200000001": 12}
    assert _production_reconcile_targets(local, fetched) == []


def test_targets_sorted_for_deterministic_logs() -> None:
    local = {"4200000009": 5, "4200000001": 5}
    fetched: dict[str, int] = {}
    assert _production_reconcile_targets(local, fetched) == [
        "4200000001",
        "4200000009",
    ]


def _prod_row(api10: str, d: date) -> ProductionMonthly:
    return ProductionMonthly(api10=api10, prod_date=d, oil_bbl=100.0)


def test_reconcile_deletes_stale_months(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end reconcile against a real (sqlite) session.

    Exercises the actual query path — this is the test that catches
    result-handling bugs the pure-function tests can't (e.g. passing a
    SQLAlchemy Result straight to dict(), which takes the mapping path
    via Result.keys() and crashes).
    """
    eng = create_engine("sqlite://")
    cast(Table, ProductionMonthly.__table__).create(eng)
    jan, feb, mar = date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)
    with Session(eng) as session:
        # Well A: 3 local months, warehouse now has only Jan.
        # Well B: 2 local months, warehouse agrees — untouched.
        # Well C: 1 local month, warehouse retracted the whole well.
        session.add_all(
            [
                _prod_row("4200000001", jan),
                _prod_row("4200000001", feb),
                _prod_row("4200000001", mar),
                _prod_row("4200000002", jan),
                _prod_row("4200000002", feb),
                _prod_row("4200000003", jan),
            ]
        )
        session.commit()

        def fake_fetch(wh: object, api10s: list[str]) -> list[ProductionRecord]:
            assert sorted(api10s) == ["4200000001", "4200000003"]
            return [ProductionRecord(api10="4200000001", prod_date=jan)]

        monkeypatch.setattr(orchestrator, "fetch_production_for_api10s", fake_fetch)
        deleted = _reconcile_production_deletions(
            session,
            wh=None,  # type: ignore[arg-type]  # fake_fetch ignores it
            fetched_counts={"4200000001": 1, "4200000002": 2},
        )
        assert deleted == 3  # A: feb+mar, C: jan

        remaining = session.execute(
            select(ProductionMonthly.api10, ProductionMonthly.prod_date).order_by(
                ProductionMonthly.api10, ProductionMonthly.prod_date
            )
        ).all()
        assert [(a, d) for a, d in remaining] == [
            ("4200000001", jan),
            ("4200000002", jan),
            ("4200000002", feb),
        ]


def test_reconcile_noop_when_counts_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = create_engine("sqlite://")
    cast(Table, ProductionMonthly.__table__).create(eng)
    with Session(eng) as session:
        session.add(_prod_row("4200000001", date(2024, 1, 1)))
        session.commit()

        def fail_fetch(wh: object, api10s: list[str]) -> list[ProductionRecord]:
            raise AssertionError("no warehouse re-fetch expected when counts agree")

        monkeypatch.setattr(orchestrator, "fetch_production_for_api10s", fail_fetch)
        deleted = _reconcile_production_deletions(
            session,
            wh=None,  # type: ignore[arg-type]
            fetched_counts={"4200000001": 1},
        )
        assert deleted == 0
        assert session.execute(select(ProductionMonthly)).scalars().all()
