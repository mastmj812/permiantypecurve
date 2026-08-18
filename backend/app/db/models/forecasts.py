from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enum_helpers import pg_enum


class Stream(str, enum.Enum):
    OIL = "oil"
    GAS = "gas"
    WATER = "water"


class FitMethod(str, enum.Enum):
    RATE_CUM = "rate_cum"
    RATE_TIME = "rate_time"
    # The default cum-fit retried with rate-time because the primary
    # pinned Di at a bound (cum has low Jacobian sensitivity to b — see
    # forecasting/fit.py::fit_with_fallback). Distinct from RATE_TIME so
    # the engineer can tell which wells were rescued.
    RATE_TIME_FALLBACK = "rate_time_fallback"
    # Short-history well whose Di / b were transferred from the median
    # of the long-history cohort in the same batch. qi stays from the
    # well's own peak. Donor cohort details are persisted in
    # `Forecast.diagnostics`. See app.forecasting.cohort.
    COHORT_TRANSFER = "cohort_transfer"
    # Ratio mode (gas/water only, engineer-selected): the stream is a
    # fitted ratio of CUMULATIVE OIL (ln r = alpha + beta*Np), forecast
    # as ratio x the oil forecast. Params live in the params JSONB
    # (mode/alpha/beta/sub_mode/...); the Arps columns are NULL. See
    # app.forecasting.ratio.
    RATIO_CUM_OIL = "ratio_cum_oil"


class ModelType(str, enum.Enum):
    ARPS_EXPONENTIAL = "arps_exponential"
    ARPS_HYPERBOLIC = "arps_hyperbolic"
    ARPS_HARMONIC = "arps_harmonic"
    MODIFIED_HYPERBOLIC = "modified_hyperbolic"
    DUONG = "duong"
    # Derived stream: ratio-vs-cumulative-oil (no Arps params of its
    # own). Only ever set on gas/water rows via the mode-switch API.
    RATIO = "ratio"


class Forecast(Base):
    """Per-well, per-stream decline forecast. Populated in step 4."""

    __tablename__ = "forecasts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # api10 (Novi wellbore identifier) is the FK as of the cutover —
    # see migration 0010. Replaces the api14 reference used pre-cutover.
    api10: Mapped[str] = mapped_column(
        String(10), ForeignKey("wells.api10", ondelete="CASCADE"), nullable=False
    )
    stream: Mapped[Stream] = mapped_column(pg_enum(Stream, name="stream"), nullable=False)

    model_type: Mapped[ModelType] = mapped_column(
        pg_enum(ModelType, name="model_type"), nullable=False
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    qi: Mapped[float | None] = mapped_column(Float)
    di_initial: Mapped[float | None] = mapped_column(Float)
    b: Mapped[float | None] = mapped_column(Float)
    df_terminal: Mapped[float | None] = mapped_column(Float)
    # Linear-ramp prefix: rate at first production (t=0) and the ramp
    # length in months from first prod to peak. The full per-well
    # forecast is now ramp(qo→qi over peak_index_months) + Arps. NULL
    # on rows persisted before this column existed and not yet re-fit
    # — the evaluator falls back to pure Arps for those.
    # See app.forecasting.ramp_arps.
    qo: Mapped[float | None] = mapped_column(Float)
    peak_index_months: Mapped[int | None] = mapped_column(Integer)

    eur: Mapped[float | None] = mapped_column(Float)
    peak_month_date: Mapped[date | None] = mapped_column(Date)
    peak_rate: Mapped[float | None] = mapped_column(Float)

    fit_method: Mapped[FitMethod] = mapped_column(
        pg_enum(FitMethod, name="fit_method"), nullable=False
    )
    fit_r2: Mapped[float | None] = mapped_column(Float)
    fit_rmse: Mapped[float | None] = mapped_column(Float)
    # Fraction of post-peak months flagged as downtime and excluded from
    # the fit (0.0 to 1.0). Lets the Review grid surface wells whose
    # forecast was built on a noisy production profile so the engineer
    # knows to eyeball them. Computed once at fit time; same value
    # across oil/gas/water streams since downtime is per-well.
    downtime_ratio: Mapped[float | None] = mapped_column(Float)
    # Audit payload for non-standard fit methods. Populated for
    # cohort_transfer rows with {source, donor_count, donor_api10s,
    # cohort_Di, cohort_b, cutoff_months} so the user can trace which
    # long-history wells contributed to a transferred forecast. Null
    # on normal rate_cum / rate_time / rate_time_fallback rows.
    diagnostics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("api10", "stream", name="uq_forecasts_api10_stream"),)
