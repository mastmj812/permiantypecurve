"""Fetch Novi's forecasted monthly series from ``curated.production_forecast``.

Mirrors ``warehouse_client.production`` but reads the forecast tail instead
of actuals. The curated view is already filtered to forecasted, non-deleted
rows, so no extra WHERE clause beyond the api10 batch is needed. One batched
query keyed on ``api10 = ANY(:api10s)``; yields ``NoviForecastRecord`` DTOs.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.warehouse_client.base import NoviForecastRecord

# Column mapping curated.production_forecast -> NoviForecastRecord. The
# per_day_* columns are Novi's calendar-day rates (same convention as
# curated.production); cumulative_* are the running totals.
_FETCH_BY_API10S_SQL = text(
    """
    SELECT
        api10,
        prod_date,
        oil_per_day_bbl         AS rate_calday_bopd,
        gas_per_day_mcf         AS rate_calday_mcfd,
        water_per_day_bbl       AS rate_calday_bwpd,
        cumulative_oil_bbl,
        cumulative_gas_mcf,
        cumulative_water_bbl
    FROM curated.production_forecast
    WHERE api10 = ANY(:api10s)
    ORDER BY api10, prod_date
    """
)


def fetch_novi_forecast_for_api10s(
    session: Session,
    api10s: Iterable[str],
) -> Iterator[NoviForecastRecord]:
    """Yield Novi forecast rows for the given api10s.

    Order: grouped by api10, then chronological. Empty input returns an
    empty iterator without hitting the DB. The caller owns the session
    lifecycle; this neither commits nor closes.
    """
    api10_list = list(api10s)
    if not api10_list:
        return

    result = session.execute(
        _FETCH_BY_API10S_SQL, {"api10s": api10_list}
    ).mappings()
    for row in result:
        yield NoviForecastRecord(
            api10=row["api10"],
            prod_date=row["prod_date"],
            rate_calday_bopd=row["rate_calday_bopd"],
            rate_calday_mcfd=row["rate_calday_mcfd"],
            rate_calday_bwpd=row["rate_calday_bwpd"],
            cumulative_oil_bbl=row["cumulative_oil_bbl"],
            cumulative_gas_mcf=row["cumulative_gas_mcf"],
            cumulative_water_bbl=row["cumulative_water_bbl"],
        )
