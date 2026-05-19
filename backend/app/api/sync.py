"""POST /api/sync/run — kicks off a background county sync.
   GET  /api/sync/status — returns most recent jobs + watermarks.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import SyncEntity, SyncJob, SyncJobStatus, SyncWatermark
from app.db.session import get_session
from app.sync.orchestrator import (
    DEFAULT_BASIN,
    DEFAULT_COUNTIES,
    DEFAULT_COUNTY,
    sync_counties,
)

router = APIRouter(prefix="/sync", tags=["sync"])
log = get_logger("api.sync")


class RunSyncRequest(BaseModel):
    basin: str = Field(default=DEFAULT_BASIN)
    # Preferred: multi-county scope. None falls through to ``county`` for
    # legacy callers, then to DEFAULT_COUNTIES.
    counties: list[str] | None = Field(default=None)
    # Legacy singular field — kept so existing curl scripts don't break.
    # If ``counties`` is set this is ignored.
    county: str | None = Field(default=None)
    pull_production: bool = Field(default=True)
    pull_surveys: bool = Field(default=True)


class RunSyncResponse(BaseModel):
    accepted: bool
    basin: str
    counties: list[str]
    note: str


class JobInfo(BaseModel):
    id: uuid.UUID
    entity: SyncEntity
    scope_key: str
    status: SyncJobStatus
    started_at: datetime | None
    finished_at: datetime | None
    items_seen: int
    items_upserted: int
    items_failed: int
    error: str | None


class WatermarkInfo(BaseModel):
    entity: SyncEntity
    scope_key: str
    last_synced_at: datetime | None


class SyncStatusResponse(BaseModel):
    recent_jobs: list[JobInfo]
    watermarks: list[WatermarkInfo]


def _resolve_counties(req: RunSyncRequest) -> tuple[str, ...]:
    # Precedence: explicit list > legacy singular > default.
    if req.counties:
        return tuple(c for c in req.counties if c)
    if req.county:
        return (req.county,)
    return DEFAULT_COUNTIES


def _run_sync_bg(req: RunSyncRequest, counties: tuple[str, ...]) -> None:
    try:
        sync_counties(
            basin=req.basin,
            counties=counties,
            pull_production=req.pull_production,
            pull_surveys=req.pull_surveys,
        )
    except Exception:
        log.exception(
            "background_sync_failed", basin=req.basin, counties=list(counties)
        )


@router.post("/run", response_model=RunSyncResponse, status_code=202)
async def run_sync(
    req: RunSyncRequest, background: BackgroundTasks
) -> RunSyncResponse:
    counties = _resolve_counties(req)
    background.add_task(_run_sync_bg, req, counties)
    return RunSyncResponse(
        accepted=True,
        basin=req.basin,
        counties=list(counties),
        note="poll GET /api/sync/status for progress",
    )


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(session: Session = Depends(get_session)) -> SyncStatusResponse:
    jobs = session.execute(
        select(SyncJob).order_by(SyncJob.created_at.desc()).limit(20)
    ).scalars().all()
    wms = session.execute(select(SyncWatermark)).scalars().all()
    return SyncStatusResponse(
        recent_jobs=[
            JobInfo(
                id=j.id,
                entity=j.entity,
                scope_key=j.scope_key,
                status=j.status,
                started_at=j.started_at,
                finished_at=j.finished_at,
                items_seen=j.items_seen,
                items_upserted=j.items_upserted,
                items_failed=j.items_failed,
                error=j.error,
            )
            for j in jobs
        ],
        watermarks=[
            WatermarkInfo(
                entity=w.entity, scope_key=w.scope_key, last_synced_at=w.last_synced_at
            )
            for w in wms
        ],
    )
