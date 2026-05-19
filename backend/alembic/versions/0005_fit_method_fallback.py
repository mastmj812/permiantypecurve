"""Add fit_method enum value 'rate_time_fallback'.

Tags forecasts where the default rate_cum fit pinned Di at a bound and
the orchestrator retried with rate_time. Distinct from plain rate_time
so the engineer can audit which wells were rescued.

Revision ID: 0005_fit_method_fallback
Revises: 0004_wells_name
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_fit_method_fallback"
down_revision: str | Sequence[str] | None = "0004_wells_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE must run outside a transaction in Postgres.
    # Alembic wraps migrations in an implicit transaction; the autocommit
    # block escapes it for this single statement.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fit_method ADD VALUE IF NOT EXISTS 'rate_time_fallback'")


def downgrade() -> None:
    # Postgres doesn't support removing enum values. Down-migration is a
    # no-op — any rows tagged 'rate_time_fallback' would need to be
    # rewritten to 'rate_time' first, which we can't safely do from
    # inside the migration without losing the audit signal.
    pass
