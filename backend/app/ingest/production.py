"""Upsert monthly production from ``warehouse_client.base.ProductionRecord``.

Bulk-inserts via ``INSERT ... ON CONFLICT (api10, prod_date) DO UPDATE``.

All rate columns (``rate_calday_*``) are direct passthroughs from the
warehouse — Novi computes calendar-day rates upstream. The legacy
app-side rate math (``ingest/rates.py``, retired in this commit) and
the month-1 ``producing_days`` exception are no longer needed: empirical
verification during the cutover audit confirmed that Novi already
applies the partial-month adjustment correctly.

Duplicate-key handling: not applicable. ``curated.production`` is
unique on (api10, prod_year, prod_month) at the warehouse layer, so the
DTOs we receive have no in-batch duplicates by construction. (Pre-
cutover, Enverus's production was keyed on ``ProductionID`` and refrac
collisions required an in-batch dedup step; that's gone now.)
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import ProductionMonthly
from app.warehouse_client.base import ProductionRecord

log = get_logger("ingest.production")


def _record_to_row(r: ProductionRecord) -> dict[str, Any]:
    return {
        "api10": r.api10,
        "prod_date": r.prod_date,
        "oil_bbl": r.oil_bbl,
        "gas_mcf": r.gas_mcf,
        "water_bbl": r.water_bbl,
        "producing_days": r.producing_days,
        "rate_calday_bopd": r.rate_calday_bopd,
        "rate_calday_mcfd": r.rate_calday_mcfd,
        "rate_calday_bwpd": r.rate_calday_bwpd,
    }


def upsert_production_records(session: Session, records: Iterable[ProductionRecord]) -> int:
    """Upsert a batch. Returns the count of rows written."""
    rows = [_record_to_row(r) for r in records]
    if not rows:
        return 0

    stmt = pg_insert(ProductionMonthly.__table__).values(rows)
    update_cols = {
        c.name: stmt.excluded[c.name]
        for c in ProductionMonthly.__table__.columns
        if c.name not in {"api10", "prod_date"}
    }
    stmt = stmt.on_conflict_do_update(index_elements=["api10", "prod_date"], set_=update_cols)
    session.execute(stmt)
    session.commit()
    log.info("upsert_production", count=len(rows))
    return len(rows)
