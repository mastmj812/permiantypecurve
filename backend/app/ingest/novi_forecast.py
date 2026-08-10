"""Upsert Novi forecast rows from ``warehouse_client.base.NoviForecastRecord``.

Bulk-inserts via ``INSERT ... ON CONFLICT (api10, prod_date) DO UPDATE``,
mirroring ``ingest.production``. The warehouse view is unique on
(api10, prod_date), so the DTOs have no in-batch duplicates by construction.

VINTAGE RULE: a Novi PDP forecast is a point-in-time snapshot — when Novi
republishes, the new vintage typically STARTS LATER (last vintage's early
"forecast" months are now actuals). Upsert alone leaves the old vintage's
early months in place, so the modal overlay / EUR-divergence highlight
would render a stitched series of two vintages with a cum discontinuity.
The sync therefore calls ``delete_novi_forecast_for_api10s`` for the
refresh scope before inserting — the table only ever holds ONE vintage
per well.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import NoviForecastMonthly
from app.warehouse_client.base import NoviForecastRecord

log = get_logger("ingest.novi_forecast")


def delete_novi_forecast_for_api10s(session: Session, api10s: list[str]) -> int:
    """Drop all existing Novi forecast rows for ``api10s``.

    Called by the sync orchestrator before re-inserting the fresh
    vintage (see module docstring). Returns the number of rows deleted.
    Does NOT commit — the caller owns the transaction so the delete
    rides with the job bookkeeping.
    """
    if not api10s:
        return 0
    result = session.execute(
        delete(NoviForecastMonthly.__table__).where(NoviForecastMonthly.api10.in_(api10s))
    )
    deleted = int(result.rowcount or 0)
    log.info("delete_novi_forecast_vintage", count=deleted)
    return deleted


def _record_to_row(r: NoviForecastRecord) -> dict[str, Any]:
    return {
        "api10": r.api10,
        "prod_date": r.prod_date,
        "rate_calday_bopd": r.rate_calday_bopd,
        "rate_calday_mcfd": r.rate_calday_mcfd,
        "rate_calday_bwpd": r.rate_calday_bwpd,
        "cumulative_oil_bbl": r.cumulative_oil_bbl,
        "cumulative_gas_mcf": r.cumulative_gas_mcf,
        "cumulative_water_bbl": r.cumulative_water_bbl,
    }


def upsert_novi_forecast_records(session: Session, records: Iterable[NoviForecastRecord]) -> int:
    """Upsert a batch. Returns the count of rows written."""
    rows = [_record_to_row(r) for r in records]
    if not rows:
        return 0

    stmt = pg_insert(NoviForecastMonthly.__table__).values(rows)
    update_cols = {
        c.name: stmt.excluded[c.name]
        for c in NoviForecastMonthly.__table__.columns
        if c.name not in {"api10", "prod_date"}
    }
    stmt = stmt.on_conflict_do_update(index_elements=["api10", "prod_date"], set_=update_cols)
    session.execute(stmt)
    session.commit()
    log.info("upsert_novi_forecast", count=len(rows))
    return len(rows)
