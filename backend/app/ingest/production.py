"""Upsert monthly production with rate computation applied on write.

Bulk-inserts batches via INSERT ... ON CONFLICT DO UPDATE. We read each
well's first_prod_date once per batch to apply the month-1 partial-month
rule in app.ingest.rates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import ProductionMonthly, Well
from app.enverus_client.base import ProductionRecord
from app.ingest.rates import RateInputs, compute_rates

log = get_logger("ingest.production")


def _first_prod_dates(session: Session, api14s: set[str]) -> dict[str, date | None]:
    if not api14s:
        return {}
    rows = session.execute(
        select(Well.api14, Well.first_prod_date).where(Well.api14.in_(api14s))
    ).all()
    return {api14: fpd for api14, fpd in rows}


def upsert_production_records(
    session: Session, records: Iterable[ProductionRecord]
) -> int:
    """Upsert in batches. Returns the count of records written."""
    # Group so we resolve first_prod_date once per well per batch.
    by_well: dict[str, list[ProductionRecord]] = defaultdict(list)
    for r in records:
        by_well[r.api14].append(r)

    if not by_well:
        return 0

    fpd_map = _first_prod_dates(session, set(by_well.keys()))
    rows: list[dict[str, Any]] = []
    for api14, recs in by_well.items():
        fpd = fpd_map.get(api14)
        for r in recs:
            rates = compute_rates(
                RateInputs(
                    prod_date=r.prod_date,
                    first_prod_date=fpd,
                    producing_days=r.producing_days,
                    oil_bbl=r.oil_bbl,
                    gas_mcf=r.gas_mcf,
                    water_bbl=r.water_bbl,
                )
            )
            rows.append(
                {
                    "api14": r.api14,
                    "prod_date": r.prod_date,
                    "oil_bbl": r.oil_bbl,
                    "gas_mcf": r.gas_mcf,
                    "water_bbl": r.water_bbl,
                    "producing_days": r.producing_days,
                    "rate_prodday_bopd": rates.rate_prodday_bopd,
                    "rate_prodday_mcfd": rates.rate_prodday_mcfd,
                    "rate_prodday_bwpd": rates.rate_prodday_bwpd,
                    "rate_calday_bopd": rates.rate_calday_bopd,
                    "rate_calday_mcfd": rates.rate_calday_mcfd,
                    "rate_calday_bwpd": rates.rate_calday_bwpd,
                    "source": r.source,
                }
            )

    stmt = pg_insert(ProductionMonthly.__table__).values(rows)
    update_cols = {
        c.name: stmt.excluded[c.name]
        for c in ProductionMonthly.__table__.columns
        if c.name not in {"api14", "prod_date"}
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["api14", "prod_date"], set_=update_cols
    )
    session.execute(stmt)
    session.commit()
    log.info("upsert_production", count=len(rows), wells=len(by_well))
    return len(rows)
