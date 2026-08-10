"""Fetch monthly production records from ``curated.production``.

Replaces ``enverus_client.PrismClient.fetch_monthly_production`` post-
cutover. One batched query keyed on ``api10 = ANY(:api10s)``; yields
api10-keyed ``ProductionRecord`` DTOs.

Streaming: the function is a generator so callers can iterate large
cohorts without materializing the full result list. A typical type-curve
cohort is ~500 wells x ~100 months = ~50k rows, which fits in memory
fine, but the generator shape keeps the option open for the orchestrator
to use larger batches.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.warehouse_client.base import ProductionRecord

# Direct column mapping from curated.production -> ProductionRecord
# fields. The per_month_* columns are the monthly volumes; per_day_*
# columns are Novi's pre-computed calendar-day rates.
#
# ORDER BY (api10, prod_date) gives consumers stable ordering. The
# curated.production unique index on (api10, prod_year, prod_month)
# supports the sort because prod_date is monotonic in (prod_year,
# prod_month) — Postgres uses the index directly.
_FETCH_BY_API10S_SQL = text(
    """
    SELECT
        api10,
        prod_date,
        oil_per_month_bbl       AS oil_bbl,
        gas_per_month_mcf       AS gas_mcf,
        water_per_month_bbl     AS water_bbl,
        producing_days,
        oil_per_day_bbl         AS rate_calday_bopd,
        gas_per_day_mcf         AS rate_calday_mcfd,
        water_per_day_bbl       AS rate_calday_bwpd
    FROM curated.production
    WHERE api10 = ANY(:api10s)
    ORDER BY api10, prod_date
    """
)


def fetch_production_for_api10s(
    session: Session,
    api10s: Iterable[str],
) -> Iterator[ProductionRecord]:
    """Yield monthly production rows for the given api10s.

    Order: grouped by api10, then chronological. Consumers that need
    per-well grouping can iterate directly or use
    ``itertools.groupby(records, key=lambda r: r.api10)``.

    Empty input returns an empty iterator without hitting the DB.
    The caller is responsible for session lifecycle; this function
    neither commits nor closes.
    """
    api10_list = list(api10s)
    if not api10_list:
        return

    result = session.execute(_FETCH_BY_API10S_SQL, {"api10s": api10_list}).mappings()
    for row in result:
        yield ProductionRecord(
            api10=row["api10"],
            prod_date=row["prod_date"],
            oil_bbl=row["oil_bbl"],
            gas_mcf=row["gas_mcf"],
            water_bbl=row["water_bbl"],
            producing_days=row["producing_days"],
            rate_calday_bopd=row["rate_calday_bopd"],
            rate_calday_mcfd=row["rate_calday_mcfd"],
            rate_calday_bwpd=row["rate_calday_bwpd"],
        )
