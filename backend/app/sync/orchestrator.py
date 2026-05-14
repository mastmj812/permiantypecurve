"""High-level sync flows: well headers, then per-well production + surveys.

Step-2 scope = Loving County only. The orchestrator persists progress to
sync_jobs / sync_watermarks so /api/sync/status can poll. SSE streaming
(brief target) wires up in step 3 once the map needs progressive updates.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import islice
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    SyncEntity,
    SyncJob,
    SyncJobStatus,
    SyncWatermark,
    Well,
)
from app.db.session import SessionLocal
from app.enverus_client.base import EnverusClient
from app.enverus_client.prism import PrismClient
from app.ingest.production import upsert_production_records
from app.ingest.surveys import upsert_survey
from app.ingest.wells import upsert_well_headers

log = get_logger("sync.orchestrator")

DEFAULT_BASIN = "Permian"
DEFAULT_COUNTY = "Loving"

T = TypeVar("T")


@contextmanager
def _job(
    session: Session,
    entity: SyncEntity,
    scope_key: str,
    metadata: dict[str, str] | None = None,
) -> Iterator[SyncJob]:
    job = SyncJob(
        id=uuid.uuid4(),
        entity=entity,
        scope_key=scope_key,
        status=SyncJobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        metadata_=metadata or None,
    )
    session.add(job)
    session.commit()
    job_id = job.id
    try:
        yield job
    except Exception as e:  # noqa: BLE001 — we want to record the message
        # The inner work may have left the session in an aborted transaction
        # (e.g. a DataError during a SQL execute). Roll back first so the
        # next statement — the failure-record write — runs in a fresh txn.
        try:
            session.rollback()
        except Exception:  # pragma: no cover  -- best-effort cleanup
            pass
        job_row = session.get(SyncJob, job_id)
        if job_row is not None:
            job_row.status = SyncJobStatus.FAILED
            job_row.error = str(e)[:2000]
            job_row.finished_at = datetime.now(timezone.utc)
            session.commit()
        log.exception("sync_job_failed", entity=entity.value, scope=scope_key)
        raise
    else:
        job.status = SyncJobStatus.SUCCEEDED
        job.finished_at = datetime.now(timezone.utc)
        session.commit()


def _watermark_set(
    session: Session, entity: SyncEntity, scope_key: str, ts: datetime
) -> None:
    row = session.get(SyncWatermark, (entity, scope_key))
    if row is None:
        row = SyncWatermark(entity=entity, scope_key=scope_key, last_synced_at=ts)
        session.add(row)
    else:
        row.last_synced_at = ts
    session.commit()


def _batched(it: Iterable[T], n: int) -> Iterator[list[T]]:
    src = iter(it)
    while True:
        chunk = list(islice(src, n))
        if not chunk:
            return
        yield chunk


def _build_client() -> EnverusClient:
    from app.config import settings

    return PrismClient(api_key=settings.enverus_api_key_prism)


def sync_county(
    *,
    basin: str = DEFAULT_BASIN,
    county: str = DEFAULT_COUNTY,
    pull_production: bool = True,
    pull_surveys: bool = True,
    client: EnverusClient | None = None,
) -> dict[str, int]:
    """Synchronous entry — called from CLI and from the background task hook.

    Returns a dict of upsert counts for the caller (and the sync status
    endpoint) to surface.
    """
    counts = {"headers": 0, "production": 0, "surveys": 0}
    cli = client or _build_client()
    scope_key = f"basin={basin};county={county}"

    # ---- 1. well headers ----
    with SessionLocal() as session:
        with _job(session, SyncEntity.WELL_HEADERS, scope_key) as job:
            header_iter = cli.fetch_well_headers(basin=basin, county=county)
            total = 0
            for batch in _batched(header_iter, 200):
                total += upsert_well_headers(session, batch)
                job.items_upserted = total
                job.items_seen = total
                session.commit()
            counts["headers"] = total
            _watermark_set(
                session, SyncEntity.WELL_HEADERS, scope_key, datetime.now(timezone.utc)
            )

    # ---- 2. production (per-well, batched) ----
    if pull_production:
        with SessionLocal() as session:
            api14s = [
                r[0]
                for r in session.execute(
                    select(Well.api14).where(Well.county == county)
                ).all()
            ]
            with _job(session, SyncEntity.PRODUCTION, scope_key) as job:
                prod_iter = cli.fetch_monthly_production(api14s)
                total = 0
                for batch in _batched(prod_iter, 5000):
                    total += upsert_production_records(session, batch)
                    job.items_upserted = total
                    job.items_seen = total
                    session.commit()
                counts["production"] = total
                _watermark_set(
                    session,
                    SyncEntity.PRODUCTION,
                    scope_key,
                    datetime.now(timezone.utc),
                )

    # ---- 3. surveys (per-well, sequential) ----
    if pull_surveys:
        with SessionLocal() as session:
            api14s = [
                r[0]
                for r in session.execute(
                    select(Well.api14).where(Well.county == county)
                ).all()
            ]
            with _job(session, SyncEntity.SURVEYS, scope_key) as job:
                for api14 in api14s:
                    job.items_seen += 1
                    survey = cli.fetch_directional_survey(api14)
                    if survey is None:
                        # Persist the progress tick anyway so the client
                        # poll sees movement.
                        session.commit()
                        continue
                    upsert_survey(session, survey)
                    job.items_upserted += 1
                    session.commit()
                counts["surveys"] = job.items_upserted
                _watermark_set(
                    session, SyncEntity.SURVEYS, scope_key, datetime.now(timezone.utc)
                )

    log.info("sync_county_done", basin=basin, county=county, **counts)
    return counts
