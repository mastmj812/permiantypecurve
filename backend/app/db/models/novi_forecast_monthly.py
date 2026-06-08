from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NoviForecastMonthly(Base):
    """Novi's forecasted (PDP) monthly series, synced from the warehouse.

    Mirrors ``ProductionMonthly`` so the ``/curves`` overlay and any future
    aggregation can reuse the same ``rate_calday_*`` column names. Sourced
    from ``curated.production_forecast`` (Novi's 50-yr forecast tail) and
    surfaced in the ForecastDetailModal as a light-blue benchmark series
    next to the actual production and the app's autoforecast.

    Composite PK ``(api10, prod_date)`` with an FK to ``wells`` (cascade
    delete) — same shape as production, but a plain table: the forecast
    tail is small relative to actuals and doesn't need a Timescale
    hypertable.

    The ``cumulative_*`` columns are stored even though the first cut only
    renders the rate overlay — keeping them now means the optional cum
    overlay is a frontend-only follow-up with no re-sync.
    """

    __tablename__ = "novi_forecast_monthly"

    api10: Mapped[str] = mapped_column(
        String(10),
        ForeignKey("wells.api10", ondelete="CASCADE"),
        primary_key=True,
    )
    prod_date: Mapped[date] = mapped_column(Date, primary_key=True)

    # Calendar-day rates — same names as ProductionMonthly so the curves
    # endpoint and charts treat this series identically. Direct passthrough
    # of curated.production_forecast.{oil,gas,water}_per_day_*.
    rate_calday_bopd: Mapped[float | None] = mapped_column(Float)
    rate_calday_mcfd: Mapped[float | None] = mapped_column(Float)
    rate_calday_bwpd: Mapped[float | None] = mapped_column(Float)

    # Novi's running cumulative totals (stored for the optional cum
    # overlay; not yet rendered).
    cumulative_oil_bbl: Mapped[float | None] = mapped_column(Float)
    cumulative_gas_mcf: Mapped[float | None] = mapped_column(Float)
    cumulative_water_bbl: Mapped[float | None] = mapped_column(Float)
