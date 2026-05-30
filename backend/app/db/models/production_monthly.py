from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductionMonthly(Base):
    """Monthly volumes plus pre-computed calday rates.

    Composite PK (api10, prod_date) — required for Timescale hypertable
    partitioning, which the initial migration applies via
    create_hypertable(). The new PK keeps prod_date as the partition
    column, so the hypertable continues to partition correctly after
    the api14 -> api10 migration (0010).

    Post-cutover from Enverus to engineering_db, the rate_prodday_*
    and source columns are retired (never read downstream; warehouse
    provides only the calday rates which Novi computes upstream).
    """

    __tablename__ = "production_monthly"

    api10: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("wells.api10", ondelete="CASCADE"),
        primary_key=True,
    )
    prod_date: Mapped[date] = mapped_column(Date, primary_key=True)

    oil_bbl: Mapped[float | None] = mapped_column(Float)
    gas_mcf: Mapped[float | None] = mapped_column(Float)
    water_bbl: Mapped[float | None] = mapped_column(Float)
    # Float because Novi/Enverus report fractional producing-days for
    # partial first months (e.g. 0.166 = ~4 hours online). Integer
    # truncation would zero-out IP-rich first-month rates.
    producing_days: Mapped[float | None] = mapped_column(Float)

    # Canonical rate columns: used by forecasting, peak detection, type
    # curve aggregation. Novi computes these calendar-day rates upstream
    # (verified during cutover audit — OilPerDay * cal_days_in_month
    # equals OilPerMonth even on partial months). The app's legacy
    # month-1 producing_days exception in ingest/rates.py is retired
    # by the cutover.
    rate_calday_bopd: Mapped[float | None] = mapped_column(Float)
    rate_calday_mcfd: Mapped[float | None] = mapped_column(Float)
    rate_calday_bwpd: Mapped[float | None] = mapped_column(Float)
