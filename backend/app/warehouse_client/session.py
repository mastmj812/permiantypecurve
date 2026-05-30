"""Read-only connection to the ``engineering_db`` warehouse.

Holds the SQLAlchemy engine + sessionmaker pair for the warehouse,
mirroring ``app/db/session.py`` but pointed at a different DSN and
locked to read-only.

Read-only is enforced at the Postgres connection level via
``default_transaction_read_only=on``. Any attempted INSERT / UPDATE /
DELETE against the warehouse raises ``ReadOnlySqlTransaction`` from
Postgres with a clear error message — much harder to violate by
accident than a code-side discipline.

The engine is created lazily (on first ``get_warehouse_session`` call)
so the app still boots when ``WAREHOUSE_DATABASE_URL`` is unset.
That matters during the cutover: the legacy Enverus orchestrator must
keep working until ``warehouse_client`` is wired into the orchestrator.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
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
    return create_engine(
        settings.warehouse_database_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        future=True,
        connect_args={
            # Postgres GUC: every connection starts in read-only transaction
            # mode. Writes raise ReadOnlySqlTransaction (SQLSTATE 25006).
            "options": "-c default_transaction_read_only=on",
            # Fail fast if the warehouse is unreachable rather than hanging
            # on the default ~75s TCP timeout.
            "connect_timeout": 5,
        },
    )


@lru_cache(maxsize=1)
def _sessionmaker() -> sessionmaker:
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
