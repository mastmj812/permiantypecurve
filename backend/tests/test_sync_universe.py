"""The well-header sync universe: every horizontal of any vintage, permits
excluded. Pins the 2026-10-07 removal of the 2010-01-01 completion floor
(it hid ~2,100 pre-2010 horizontals) without touching the warehouse."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.sync.orchestrator import DEFAULT_FIRST_COMPLETION_AFTER, DEFAULT_FIRST_PROD_AFTER
from app.warehouse_client.wells import fetch_well_headers

PERMIT_CLAUSE = (
    "(we.first_completion_date IS NOT NULL OR COALESCE(we.well_status, '') NOT ILIKE 'Permit%')"
)


class _CapturingSession:
    """Records the SQL + params and returns no rows."""

    def __init__(self) -> None:
        self.sql: str | None = None
        self.params: dict[str, Any] | None = None

    def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
        self.sql = str(stmt)
        self.params = params

        class _Result:
            @staticmethod
            def mappings() -> list[Any]:
                return []

        return _Result()


def test_default_universe_has_no_vintage_floor() -> None:
    sess = _CapturingSession()
    list(fetch_well_headers(sess))  # type: ignore[arg-type]
    assert sess.sql is not None and sess.params is not None
    assert PERMIT_CLAUSE in sess.sql
    assert "we.is_horizontal = TRUE" in sess.sql
    assert ":first_completion_after" not in sess.sql
    assert "first_completion_after" not in sess.params


def test_explicit_floor_keeps_null_completion_and_permit_exclusion() -> None:
    sess = _CapturingSession()
    list(fetch_well_headers(sess, first_completion_after=date(2010, 1, 1)))  # type: ignore[arg-type]
    assert sess.sql is not None and sess.params is not None
    assert PERMIT_CLAUSE in sess.sql
    assert (
        "(we.first_completion_date >= :first_completion_after OR we.first_completion_date IS NULL)"
    ) in sess.sql
    assert sess.params["first_completion_after"] == date(2010, 1, 1)


def test_orchestrator_defaults_pass_no_floor() -> None:
    assert DEFAULT_FIRST_COMPLETION_AFTER is None
    assert DEFAULT_FIRST_PROD_AFTER is None
