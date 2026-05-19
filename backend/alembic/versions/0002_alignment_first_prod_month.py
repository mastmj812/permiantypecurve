"""add first_prod_month to alignment_method enum

Revision ID: 0002_alignment_first_prod_month
Revises: 0001_initial
Create Date: 2026-05-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_alignment_first_prod_month"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres rejects ALTER TYPE ... ADD VALUE inside a transaction block;
    # Alembic's autocommit_block escapes the surrounding transaction so the
    # DDL can land. Subsequent rows can reference the new value immediately.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE alignment_method ADD VALUE IF NOT EXISTS 'first_prod_month'"
        )


def downgrade() -> None:
    # Postgres has no clean way to drop a single enum value; doing so requires
    # recreating the enum type and rewriting every column that uses it. For
    # local dev we accept that downgrade is a no-op — the value remains.
    pass
