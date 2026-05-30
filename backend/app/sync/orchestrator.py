"""High-level sync: bulk-load wells + production from engineering_db.

Replaces the legacy Enverus-driven per-county sync. The warehouse holds
the entire Permian; one sync covers it in a single read pass for the
well headers, then production is pulled in batches keyed by the api10
list we just loaded.

Sync flow:
    1. ``fetch_well_headers`` from ``curated.wells_enriched`` (Permian-
       wide, vintage 2010+ horizontals by default), stream into the
       local ``wells`` table via ``upsert_well_headers``.
    2. ``fetch_production_for_api10s`` from ``curated.production`` for
       all just-loaded api10s, stream into ``production_monthly``.

Each phase gets its own ``SyncJob`` row (entity =
``SyncEntity.WELL_HEADERS`` then ``SyncEntity.PRODUCTION``). The
``scope_key`` is ``"env_region=PERMIAN"`` since the sync no longer
splits by county.

Back-compat: ``sync_county`` and ``sync_counties`` are kept as thin
wrappers so the existing CLI (``app.seed.seed_county``) and HTTP
endpoint (``app.api.sync``) continue to function without modification.
They both log a deprecation note and route to ``sync_permian``. The
``counties``/``county`` args are accepted-and-ignored — the warehouse
sync is Permian-wide by design.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
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
from app.ingest.production import upsert_production_records
from app.ingest.wells import upsert_well_headers
from app.warehouse_client.production import fetch_production_for_api10s
from app.warehouse_client.session import _engine as _warehouse_engine
from app.warehouse_client.wells import fetch_well_headers

log = get_logger("sync.orchestrator")


# Defaults that drive the warehouse query. ``DEFAULT_BASIN`` /
# ``DEFAULT_COUNTY`` / ``DEFAULT_COUNTIES`` are preserved so external
# importers (api/sync.py, seed_county.py) don't break on import; their
# values are now informational only.
DEFAULT_BASIN: str = "Permian"
DEFAULT_COUNTY: str | None = None
DEFAULT_COUNTIES: tuple[str, ...] = ()
DEFAULT_FIRST_PROD_AFTER: date = date(2010, 1, 1)
DEFAULT_FIRST_COMPLETION_AFTER: date = date(2010, 1, 1)
DEFAULT_MIN_LATERAL_FT: float | None = None
DEFAULT_HORIZONTAL_ONLY: bool = True

# Single scope key for SyncJob / SyncWatermark — one sync per run, one
# scope (the entire Permian).
SCOPE_KEY: str = "env_region=PERMIAN"

T = TypeVar("T")


# ----------------------------------------------------------------------
# Job + watermark helpers (unchanged from the Enverus orchestrator)
# ----------------------------------------------------------------------


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
    except Exception as e:  # noqa: BLE001
        try:
            session.rollback()
        except Exception:  # pragma: no cover
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


# ----------------------------------------------------------------------
# Main entry: sync_permian
# ----------------------------------------------------------------------


def sync_permian(
    *,
    pull_production: bool = True,
    first_completion_after: date | None = DEFAULT_FIRST_COMPLETION_AFTER,
    horizontal_only: bool = DEFAULT_HORIZONTAL_ONLY,
) -> dict[str, int]:
    """Sync the entire Permian from engineering_db into the local app DB.

    Returns a count dict ``{"headers": N, "production": M}`` for the
    caller to surface.

    Pulling all-Permian production (~5M rows) takes minutes. If the
    caller wants only the well headers refreshed (e.g. nightly map
    refresh without per-month delta), pass ``pull_production=False``.
    """
    counts = {"headers": 0, "production": 0}

    wh_engine = _warehouse_engine()

    # ---- 1. Well headers ----
    with SessionLocal() as session:
        with _job(session, SyncEntity.WELL_HEADERS, SCOPE_KEY) as job:
            with Session(wh_engine) as wh:
                header_iter = fetch_well_headers(
                    wh,
                    first_completion_after=first_completion_after,
                    horizontal_only=horizontal_only,
                )
                total = 0
                # 200/batch keeps Postgres' bind-parameter count well
                # below the 65 535 limit even with the ON CONFLICT SET
                # clause expanding the column list. The ingest layer
                # commits per batch, so progress is durable.
                for batch in _batched(header_iter, 200):
                    total += upsert_well_headers(session, batch)
                    job.items_upserted = total
                    job.items_seen = total
                    session.commit()
                counts["headers"] = total
            _watermark_set(
                session,
                SyncEntity.WELL_HEADERS,
                SCOPE_KEY,
                datetime.now(timezone.utc),
            )

    # ---- 2. Production ----
    if pull_production:
        with SessionLocal() as session:
            # Read the api10 list AFTER headers committed so we cover
            # every well just loaded.
            api10s = [
                r[0]
                for r in session.execute(select(Well.api10)).all()
            ]
            with _job(session, SyncEntity.PRODUCTION, SCOPE_KEY) as job:
                with Session(wh_engine) as wh:
                    prod_iter = fetch_production_for_api10s(wh, api10s)
                    total = 0
                    # 1 000/batch × 9 columns ≈ 9 000 bind params per
                    # statement — comfortable margin under the 65 535
                    # ceiling.
                    for batch in _batched(prod_iter, 1000):
                        total += upsert_production_records(session, batch)
                        job.items_upserted = total
                        job.items_seen = total
                        session.commit()
                    counts["production"] = total
                _watermark_set(
                    session,
                    SyncEntity.PRODUCTION,
                    SCOPE_KEY,
                    datetime.now(timezone.utc),
                )

    log.info("sync_permian_done", **counts)
    return counts


# ----------------------------------------------------------------------
# Back-compat wrappers (CLI + API still import these symbols)
# ----------------------------------------------------------------------


def sync_county(
    *,
    basin: str = DEFAULT_BASIN,
    county: str | None = None,
    pull_production: bool = True,
    first_prod_after: date | None = DEFAULT_FIRST_PROD_AFTER,
    min_lateral_ft: float | None = DEFAULT_MIN_LATERAL_FT,
    client: object | None = None,
) -> dict[str, int]:
    """Deprecated. Routes to ``sync_permian``.

    Pre-cutover, this synced one county at a time via Enverus.
    Post-cutover, the warehouse pulls the entire Permian in one shot
    and per-county scope is obsolete. ``basin``, ``county``,
    ``min_lateral_ft``, and ``client`` are accepted-and-ignored;
    ``first_prod_after`` is honored as ``first_completion_after``.
    """
    log.warning(
        "sync_county_deprecated_routing_to_permian",
        ignored_basin=basin,
        ignored_county=county,
    )
    return sync_permian(
        pull_production=pull_production,
        first_completion_after=first_prod_after,
    )


def sync_counties(
    *,
    basin: str = DEFAULT_BASIN,
    counties: tuple[str, ...] | list[str] = DEFAULT_COUNTIES,
    pull_production: bool = True,
    first_prod_after: date | None = DEFAULT_FIRST_PROD_AFTER,
    min_lateral_ft: float | None = DEFAULT_MIN_LATERAL_FT,
    client: object | None = None,
) -> dict[str, dict[str, int]]:
    """Deprecated. Routes to a single ``sync_permian`` call.

    Pre-cutover, this looped per county. Now it's one Permian-wide sync.
    The return shape ``{county: counts}`` is preserved for back-compat
    with the API response model; a single synthetic key ``"PERMIAN"``
    carries the totals.
    """
    log.warning(
        "sync_counties_deprecated_routing_to_permian",
        ignored_basin=basin,
        ignored_counties=list(counties),
    )
    counts = sync_permian(
        pull_production=pull_production,
        first_completion_after=first_prod_after,
    )
    return {"PERMIAN": counts}
