from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enum_helpers import pg_enum


class SyncEntity(str, enum.Enum):
    WELL_HEADERS = "well_headers"
    PRODUCTION = "production"
    SURVEYS = "surveys"


class SyncJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SyncWatermark(Base):
    """One row per (entity, scope) — scope is "basin=permian" or
    "county=Loving" etc, JSON for flexibility. `last_synced_at` is the
    high-water mark we send to Enverus as `updatedSince` on the next pull.
    """

    __tablename__ = "sync_watermarks"

    entity: Mapped[SyncEntity] = mapped_column(
        pg_enum(SyncEntity, name="sync_entity"), primary_key=True
    )
    scope_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SyncJob(Base):
    """One row per sync run. Polled by GET /api/sync/status for progress."""

    __tablename__ = "sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity: Mapped[SyncEntity] = mapped_column(
        pg_enum(SyncEntity, name="sync_entity"), nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SyncJobStatus] = mapped_column(
        pg_enum(SyncJobStatus, name="sync_job_status"),
        default=SyncJobStatus.PENDING,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_upserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error: Mapped[str | None] = mapped_column(String(2000))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
