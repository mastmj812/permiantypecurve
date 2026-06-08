"""Upsert Novi forecast rows from ``warehouse_client.base.NoviForecastRecord``.

Bulk-inserts via ``INSERT ... ON CONFLICT (api10, prod_date) DO UPDATE``,
mirroring ``ingest.production``. The warehouse view is unique on
(api10, prod_date), so the DTOs have no in-batch duplicates by construction.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import NoviForecastMonthly
from app.warehouse_client.base import NoviForecastRecord

log = get_logger("ingest.novi_forecast")


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


def upsert_novi_forecast_records(
    session: Session, records: Iterable[NoviForecastRecord]
) -> int:
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
    stmt = stmt.on_conflict_do_update(
        index_elements=["api10", "prod_date"], set_=update_cols
    )
    session.execute(stmt)
    session.commit()
    log.info("upsert_novi_forecast", count=len(rows))
    return len(rows)
