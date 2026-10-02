"""Deal-scoped PDP forecasting from seller VDR daily production.

Two tables, both separate from the global per-well ``forecasts`` table on
purpose (decision 2026-10-01): ``forecasts`` is one row per (api10,
stream) on the warehouse (Novi monthly) basis and feeds type-curve
cohorts; a VDR-based fit written there would silently move every type
curve that uses the well as an analog onto a different data basis.

* ``vdr_daily_production`` — local copy of warehouse ``vdr.well_daily``
  for one data room (synced on demand per deal, replaced wholesale).
* ``pdp_forecasts`` — one row per (deal, api10, stream). See
  ``app.forecasting.daily`` for the method of record.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enum_helpers import pg_enum
from app.db.models.forecasts import Stream


class VdrDailyProduction(Base):
    __tablename__ = "vdr_daily_production"

    vdr_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    api10: Mapped[str] = mapped_column(String(10), primary_key=True)
    prod_date: Mapped[date] = mapped_column(Date, primary_key=True)
    well_name: Mapped[str | None] = mapped_column(String(255))
    reserve_category: Mapped[str | None] = mapped_column(String(32))
    oil_bbl: Mapped[float | None] = mapped_column(Float)
    gas_mcf: Mapped[float | None] = mapped_column(Float)
    water_bbl: Mapped[float | None] = mapped_column(Float)
    tbg_psi: Mapped[float | None] = mapped_column(Float)
    csg_psi: Mapped[float | None] = mapped_column(Float)
    choke: Mapped[float | None] = mapped_column(Float)
    bhp_psi: Mapped[float | None] = mapped_column(Float)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PdpForecast(Base):
    """One stream's PDP forecast for one well within one deal.

    ``params`` (qi/Di/b/Df, modified hyperbolic, Di NOMINAL per year) are
    in PRODUCING-DAY rate units with t = 0 at ``anchor_date`` (the
    stream's own peak for a ``daily_fit``; data_through for a
    ``transfer_now``). Calendar volumes = model rate x ``uptime_factor``.
    """

    __tablename__ = "pdp_forecasts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False
    )
    vdr_id: Mapped[str] = mapped_column(String(64), nullable=False)
    api10: Mapped[str] = mapped_column(String(10), nullable=False)
    stream: Mapped[Stream] = mapped_column(
        pg_enum(Stream, name="stream", create_type=False), nullable=False
    )
    # daily_fit | transfer_now | manual
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    qi: Mapped[float] = mapped_column(Float, nullable=False)
    di_initial: Mapped[float] = mapped_column(Float, nullable=False)
    b: Mapped[float] = mapped_column(Float, nullable=False)
    df_terminal: Mapped[float] = mapped_column(Float, nullable=False)
    anchor_date: Mapped[date] = mapped_column(Date, nullable=False)
    fit_start_date: Mapped[date | None] = mapped_column(Date)
    data_through: Mapped[date] = mapped_column(Date, nullable=False)
    uptime_factor: Mapped[float] = mapped_column(Float, nullable=False)
    uptime_basis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fit_r2_log: Mapped[float | None] = mapped_column(Float)
    fit_rmse_log: Mapped[float | None] = mapped_column(Float)
    n_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tail_ratio: Mapped[float | None] = mapped_column(Float)
    cum_to_date: Mapped[float] = mapped_column(Float, nullable=False)
    remaining: Mapped[float] = mapped_column(Float, nullable=False)
    eur: Mapped[float] = mapped_column(Float, nullable=False)
    review_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    breaks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("deal_id", "api10", "stream", name="uq_pdp_forecasts_deal_api10_stream"),
        Index("ix_pdp_forecasts_deal_id", "deal_id"),
    )
