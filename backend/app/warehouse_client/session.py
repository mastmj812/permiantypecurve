"""Read-only connection to the ``engineering_db`` warehouse.

Holds the SQLAlchemy engine + sessionmaker pair for the warehouse,
mirroring ``app/db/session.py`` but pointed at a different DSN and
locked to read-only.

Read-only is enforced at the Postgres level via a per-transaction
``SET TRANSACTION READ ONLY``. Any attempted INSERT / UPDATE / DELETE
against the warehouse raises ``ReadOnlySqlTransaction`` from Postgres
with a clear error message — much harder to violate by accident than a
code-side discipline.

The DSN points at Supabase's Supavisor TRANSACTION pooler (port 6543)
— the mandated app-tier endpoint (the 5432 session pooler is reserved
for ETL; apps stranding sessions there on backend reloads is the
15-slot-hang failure mode). Transaction pooling constrains how GUCs
can be applied: server connections are multiplexed between
transactions, so a session-level ``SET`` issued at connect time lands
on whichever server connection happened to serve it and silently does
not follow this client. Hence the per-transaction "begin" hook below
(erebor's ``backend/app/db.py`` is the reference implementation) and
``prepare_threshold=None`` (server-side prepared statements target a
specific server connection and break under multiplexing).

The engine is created lazily (on first ``get_warehouse_session`` call)
so the app still boots when ``WAREHOUSE_DATABASE_URL`` is unset.
That matters during the cutover: the legacy Enverus orchestrator must
keep working until ``warehouse_client`` is wired into the orchestrator.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_NOT_CONFIGURED_MESSAGE = (
    "WAREHOUSE_DATABASE_URL is not set; cannot read from engineering_db. "
    "Set it in .env (see .env.example for the canonical Docker-Desktop "
    "value using host.docker.internal)."
)


@lru_cache(maxsize=1)
def _engine() -> Engine:
    """Build the warehouse engine on first use; cache process-wide."""
    if not settings.warehouse_database_url:
        raise RuntimeError(_NOT_CONFIGURED_MESSAGE)
    engine = create_engine(
        settings.warehouse_database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        future=True,
        connect_args={
            # SSL required by hosted Postgres (Supabase); harmless on local PG.
            "sslmode": "require",
            # Fail fast if the warehouse is unreachable rather than hanging
            # on the default ~75s TCP timeout.
            "connect_timeout": 5,
            # Mandatory on the 6543 transaction pooler: psycopg's automatic
            # server-side prepared statements pin to one server connection,
            # which the pooler swaps between transactions -> "prepared
            # statement does not exist". Harmless on direct connections.
            "prepare_threshold": None,
            # Keep long, quiet reads alive through a connection pooler.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )

    @event.listens_for(engine, "begin")
    def _warehouse_txn_setup(conn: Connection) -> None:
        # Per-TRANSACTION GUCs — the only kind that reliably stick through
        # the 6543 transaction pooler (session-level SETs land on a server
        # connection the pooler may hand to someone else; Supavisor forwards
        # startup `options` search_path but drops statement_timeout and
        # read-only). SET TRANSACTION / SET LOCAL scope to the current
        # transaction, which the pooler pins to one server connection:
        #  - READ ONLY: writes raise ReadOnlySqlTransaction (SQLSTATE 25006).
        #  - statement_timeout=0: large curated.* streams (production ~4M
        #    rows, production_forecast ~19M) must not be killed mid-read;
        #    SET LOCAL overrides the hosted platform default for this txn.
        #  - search_path includes `extensions` so PostGIS types resolve
        #    (Supabase installs PostGIS into the extensions schema).
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = 0")
        conn.exec_driver_sql("SET LOCAL search_path TO public, extensions")

    return engine


@lru_cache(maxsize=1)
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        bind=_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_warehouse_session() -> Iterator[Session]:
    """FastAPI dependency: yield a read-only Session against the warehouse.

    Usage::

        from fastapi import Depends
        from sqlalchemy.orm import Session

        from app.warehouse_client.session import get_warehouse_session


        @router.get("/wells/{api10}")
        def get_well(
            api10: str,
            wh: Session = Depends(get_warehouse_session),
        ) -> WellOut: ...

    The session is closed automatically; do NOT call ``wh.commit()`` — the
    Postgres GUC makes commits no-ops on read queries and errors on writes.
    """
    s = _sessionmaker()()
    try:
        yield s
    finally:
        s.close()
