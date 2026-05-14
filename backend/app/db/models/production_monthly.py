from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProductionMonthly(Base):
    """Monthly volumes plus pre-computed calday and prodday rates.

    Composite PK (api14, prod_date) — required for Timescale hypertable
    partitioning, which the initial migration applies via create_hypertable().
    """

    __tablename__ = "production_monthly"

    api14: Mapped[str] = mapped_column(
        String(14), ForeignKey("wells.api14", ondelete="CASCADE"), primary_key=True
    )
    prod_date: Mapped[date] = mapped_column(Date, primary_key=True)

    oil_bbl: Mapped[float | None] = mapped_column(Float)
    gas_mcf: Mapped[float | None] = mapped_column(Float)
    water_bbl: Mapped[float | None] = mapped_column(Float)
    producing_days: Mapped[int | None] = mapped_column(Integer)

    # Diagnostic only — DO NOT use in forecasting or type curve aggregation.
    rate_prodday_bopd: Mapped[float | None] = mapped_column(Float)
    rate_prodday_mcfd: Mapped[float | None] = mapped_column(Float)
    rate_prodday_bwpd: Mapped[float | None] = mapped_column(Float)

    # Canonical rate columns: used by forecasting, peak detection, type
    # curve aggregation. Month-1 partial-month exception is applied at write
    # time in app.ingest.rates.
    rate_calday_bopd: Mapped[float | None] = mapped_column(Float)
    rate_calday_mcfd: Mapped[float | None] = mapped_column(Float)
    rate_calday_bwpd: Mapped[float | None] = mapped_column(Float)

    source: Mapped[str | None] = mapped_column(String(32))
