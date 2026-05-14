from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DirectionalSurveyStation(Base):
    """One row per survey station. Composite PK (api14, station_seq) keeps the
    survey ordered and lets ingest do idempotent upserts."""

    __tablename__ = "directional_surveys"

    api14: Mapped[str] = mapped_column(
        String(14), ForeignKey("wells.api14", ondelete="CASCADE"), primary_key=True
    )
    station_seq: Mapped[int] = mapped_column(Integer, primary_key=True)

    md_ft: Mapped[float] = mapped_column(Float, nullable=False)
    inclination_deg: Mapped[float] = mapped_column(Float, nullable=False)
    azimuth_deg: Mapped[float | None] = mapped_column(Float)
    tvd_ft: Mapped[float | None] = mapped_column(Float)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
