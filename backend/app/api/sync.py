"""POST /api/sync/run — kicks off a background county sync.
   GET  /api/sync/status — returns most recent jobs + watermarks.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import SyncEntity, SyncJob, SyncJobStatus, SyncWatermark
from app.db.session import get_session
from app.sync.orchestrator import (
    DEFAULT_BASIN,
    DEFAULT_COUNTIES,
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
    # Legacy: the survey-fetch phase was retired in Phase 2 of the
    # LateralLine migration. Field kept so old curl scripts don't 422
    # on a stale request body; it has no effect.
    pull_surveys: bool = Field(default=True, deprecated=True)


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


@router.get("/jobs/{job_id}", response_model=JobInfo)
async def get_sync_job(
    job_id: uuid.UUID, session: Session = Depends(get_session)
) -> JobInfo:
    """Fetch a single sync job by id.

    The polling path on the frontend uses this rather than scanning
    ``/sync/status``, which is capped at 20 most-recent rows — a busy
    session can push an in-flight job off that window and leave the
    poller spinning forever. Direct id lookup avoids that race.
    """
    job = session.get(SyncJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="not found")
    return JobInfo(
        id=job.id,
        entity=job.entity,
        scope_key=job.scope_key,
        status=job.status,
        started_at=job.started_at,
        finished_at=job.finished_at,
        items_seen=job.items_seen,
        items_upserted=job.items_upserted,
        items_failed=job.items_failed,
        error=job.error,
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
