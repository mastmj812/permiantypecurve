from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enum_helpers import pg_enum


class NormalizationBasis(str, enum.Enum):
    PER_LATERAL_FT = "per_lateral_ft"
    PER_PROPPANT_LB = "per_proppant_lb"
    PER_WELL = "per_well"


class AlignmentMethod(str, enum.Enum):
    PEAK_MONTH = "peak_month"


class TypeCurve(Base):
    """A saved type curve aggregation. Populated in step 6.

    `series` is the full per-month P10/P25/P50/P75/P90/mean + well-count
    breakdown for oil/gas/water, snapshotted at save time so a recompute
    on changing inputs doesn't silently mutate saved versions.
    """

    __tablename__ = "type_curves"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    filter_spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    included_api14s: Mapped[list[str]] = mapped_column(
        ARRAY(String(14)), nullable=False
    )

    normalization_basis: Mapped[NormalizationBasis] = mapped_column(
        pg_enum(NormalizationBasis, name="normalization_basis"), nullable=False
    )
    alignment_method: Mapped[AlignmentMethod] = mapped_column(
        pg_enum(AlignmentMethod, name="alignment_method"), nullable=False
    )

    series: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Self-ref pointer: "save as new version of X" sets version_of = X.id.
    version_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("type_curves.id", ondelete="SET NULL")
    )
