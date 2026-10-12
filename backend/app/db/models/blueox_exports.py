"""Log of Blue Ox curve-drop exports + their #eng-updates notices.

One row per successful ``POST /api/deals/{id}/blueox-export.xlsx``. The
row remembers what the workbook carried (``summary``: zones, location
counts, risk multipliers) so the "Notify Blue Ox" draft can name the
superseded file and the zone/count changes without re-reading an xlsx
that lives on the engineer's OneDrive. ``notified_*`` is stamped when
the notice is posted; the previously NOTIFIED export is what Steven
holds, so it — not merely the previous export — is what a new drop
supersedes.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BlueOxExport(Base):
    __tablename__ = "blueox_exports"
    __table_args__ = (Index("ix_blueox_exports_deal_created", "deal_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    export_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Built with allow_stale=true — never notified (blueox-curve-drop skill).
    allow_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Blue Ox codename as posted (the ArcMap / Deal Folder name, e.g.
    # "RALLY CAPS") — not the lowercase config codename in the filename.
    notified_codename: Mapped[str | None] = mapped_column(String(255))
    notified_text: Mapped[str | None] = mapped_column(Text)
    notified_by: Mapped[str | None] = mapped_column(String(255))
