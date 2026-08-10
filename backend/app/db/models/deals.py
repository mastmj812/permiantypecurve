from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Deal(Base):
    """A grouping of type curves for a single acquisition / divestiture.

    Type curves point back via `type_curves.deal_id` (nullable, ON DELETE
    SET NULL). One deal typically contains a curve per relevant formation
    in the package (e.g. holdTheLine → bs1s_v2, bs2s_v2). The deal-level
    Excel export bundles each assigned curve's metadata + 50-year fitted
    forecast into a single workbook.
    """

    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Persisted Blue Ox curve-drop configuration (migration 0024): the
    # JSON dump of a BlueOxExportRequest — kickoff params, zone list
    # (cross-deal curves allowed), narvi scenario selections with pinned
    # updated_at stamps. One deal = one Blue Ox codename = one governing
    # workbook; the export endpoint runs from this when no ad-hoc
    # payload is supplied.
    blueox_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
