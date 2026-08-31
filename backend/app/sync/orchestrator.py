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

import contextlib
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from itertools import islice

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    ProductionMonthly,
    SyncEntity,
    SyncJob,
    SyncJobStatus,
    SyncWatermark,
    Well,
)
from app.db.session import SessionLocal
from app.ingest.novi_forecast import (
    delete_novi_forecast_for_api10s,
    upsert_novi_forecast_records,
)
from app.ingest.production import upsert_production_records
from app.ingest.wells import upsert_well_headers
from app.warehouse_client.novi_forecast import fetch_novi_forecast_for_api10s
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
# Distinct watermark scope for the Novi-forecast phase so it doesn't
# clobber the actual-production watermark (both ride SyncEntity.PRODUCTION
# — there's no dedicated enum value, mirroring the forecast-batch jobs
# which tag kind via metadata to avoid an enum migration).
NOVI_FORECAST_SCOPE_KEY: str = "env_region=PERMIAN;kind=novi_forecast"

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
        started_at=datetime.now(UTC),
        metadata_=metadata or None,
    )
    session.add(job)
    session.commit()
    job_id = job.id
    try:
        yield job
    except Exception as e:
        with contextlib.suppress(Exception):  # pragma: no cover
            session.rollback()
        job_row = session.get(SyncJob, job_id)
        if job_row is not None:
            job_row.status = SyncJobStatus.FAILED
            job_row.error = str(e)[:2000]
            job_row.finished_at = datetime.now(UTC)
            session.commit()
        log.exception("sync_job_failed", entity=entity.value, scope=scope_key)
        raise
    else:
        job.status = SyncJobStatus.SUCCEEDED
        job.finished_at = datetime.now(UTC)
        session.commit()


def _watermark_set(session: Session, entity: SyncEntity, scope_key: str, ts: datetime) -> None:
    row = session.get(SyncWatermark, (entity, scope_key))
    if row is None:
        row = SyncWatermark(entity=entity, scope_key=scope_key, last_synced_at=ts)
        session.add(row)
    else:
        row.last_synced_at = ts
    session.commit()


def _batched[T](it: Iterable[T], n: int) -> Iterator[list[T]]:
    src = iter(it)
    while True:
        chunk = list(islice(src, n))
        if not chunk:
            return
        yield chunk


def _production_reconcile_targets(
    local_counts: dict[str, int],
    fetched_counts: dict[str, int],
) -> list[str]:
    """Wells whose local month count disagrees with what the warehouse
    just returned.

    Because the upsert runs first, local is a superset of fetched per
    well — a mismatch means the local table holds months the warehouse
    no longer publishes (Novi allocation retractions, wells dropped from
    the producing set). A well absent from ``fetched_counts`` entirely
    (0 warehouse rows) is a target too: its whole local history is
    retracted. Pure function so the decision rule is unit-testable.
    """
    return sorted(
        a for a, n in local_counts.items() if fetched_counts.get(a, 0) != n
    )


def _reconcile_production_deletions(
    session: Session,
    wh: Session,
    fetched_counts: dict[str, int],
) -> int:
    """Delete local production months the warehouse no longer has.

    The production upsert never deletes, so retracted vendor months
    linger locally and quietly poison fits (the chronic form of the
    2024-08 pad-spike incident). ``production_monthly`` is a vendor
    mirror — the warehouse is the source of truth and every deleted row
    is regenerable from it; user-authored rows (forecasts, overrides)
    are untouched.

    Strategy: compare per-well row counts local-vs-fetched, then
    re-fetch the current key set for just the mismatched wells (a
    handful on a routine night) and delete rows outside it. Returns the
    number of rows deleted.
    """
    local_counts: dict[str, int] = dict(
        session.execute(
            select(ProductionMonthly.api10, func.count()).group_by(ProductionMonthly.api10)
        ).tuples()
    )
    targets = _production_reconcile_targets(local_counts, fetched_counts)
    if not targets:
        return 0

    keep: dict[str, set[date]] = {a: set() for a in targets}
    for rec in fetch_production_for_api10s(wh, targets):
        keep[rec.api10].add(rec.prod_date)

    deleted = 0
    for api10 in targets:
        dates = keep[api10]
        stmt = delete(ProductionMonthly).where(ProductionMonthly.api10 == api10)
        if dates:
            stmt = stmt.where(ProductionMonthly.prod_date.not_in(dates))
        res = session.execute(stmt.execution_options(synchronize_session=False))
        # CursorResult at runtime; ORM execute() is typed Result[Any].
        deleted += res.rowcount  # type: ignore[attr-defined]
    session.commit()
    log.info("production_reconcile_deleted", wells=len(targets), rows=deleted)
    return deleted


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
    counts = {"headers": 0, "production": 0, "production_deleted": 0, "novi_forecast": 0}

    wh_engine = _warehouse_engine()

    # ---- 1. Well headers ----
    with SessionLocal() as session, _job(session, SyncEntity.WELL_HEADERS, SCOPE_KEY) as job:
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
            for header_batch in _batched(header_iter, 200):
                total += upsert_well_headers(session, header_batch)
                job.items_upserted = total
                job.items_seen = total
                session.commit()
            counts["headers"] = total
        _watermark_set(
            session,
            SyncEntity.WELL_HEADERS,
            SCOPE_KEY,
            datetime.now(UTC),
        )

    # ---- 2. Production ----
    if pull_production:
        with SessionLocal() as session:
            # Read the api10 list AFTER headers committed so we cover
            # every well just loaded.
            api10s = [r[0] for r in session.execute(select(Well.api10)).all()]
            with _job(session, SyncEntity.PRODUCTION, SCOPE_KEY) as job:
                with Session(wh_engine) as wh:
                    prod_iter = fetch_production_for_api10s(wh, api10s)
                    total = 0
                    # Per-well fetched-row tally, fed to the deletion
                    # reconcile below. ~67k keys — negligible memory.
                    fetched_counts: dict[str, int] = {}
                    # 1 000/batch × 9 columns ≈ 9 000 bind params per
                    # statement — comfortable margin under the 65 535
                    # ceiling.
                    for prod_batch in _batched(prod_iter, 1000):
                        for rec in prod_batch:
                            fetched_counts[rec.api10] = fetched_counts.get(rec.api10, 0) + 1
                        total += upsert_production_records(session, prod_batch)
                        job.items_upserted = total
                        job.items_seen = total
                        session.commit()
                    counts["production"] = total
                    # The upsert never deletes; reap months the
                    # warehouse no longer publishes so retracted vendor
                    # data can't linger and poison fits.
                    counts["production_deleted"] = _reconcile_production_deletions(
                        session, wh, fetched_counts
                    )
                _watermark_set(
                    session,
                    SyncEntity.PRODUCTION,
                    SCOPE_KEY,
                    datetime.now(UTC),
                )

            # ---- 3. Novi forecast (PDP) ----
            # Parallel to production: same api10 batch, separate job +
            # watermark scope. Rides SyncEntity.PRODUCTION with a
            # kind=novi_forecast metadata tag (no dedicated enum value).
            with _job(
                session,
                SyncEntity.PRODUCTION,
                NOVI_FORECAST_SCOPE_KEY,
                metadata={"kind": "novi_forecast"},
            ) as job:
                with Session(wh_engine) as wh:
                    # Vintage rule: wipe the refresh scope first so the
                    # table holds exactly ONE Novi vintage per well —
                    # upsert alone leaves the previous vintage's early
                    # months behind when the new snapshot starts later,
                    # and the overlay then renders a stitched
                    # two-vintage series with a cum discontinuity.
                    # Tradeoff: a crash between this delete and the
                    # inserts leaves those wells with no Novi rows
                    # until the next successful sync — visibly absent
                    # beats subtly wrong, and a re-run repairs it.
                    delete_novi_forecast_for_api10s(session, api10s)
                    fc_iter = fetch_novi_forecast_for_api10s(wh, api10s)
                    total = 0
                    for fc_batch in _batched(fc_iter, 1000):
                        total += upsert_novi_forecast_records(session, fc_batch)
                        job.items_upserted = total
                        job.items_seen = total
                        session.commit()
                    counts["novi_forecast"] = total
                _watermark_set(
                    session,
                    SyncEntity.PRODUCTION,
                    NOVI_FORECAST_SCOPE_KEY,
                    datetime.now(UTC),
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
