"""Add deals table + type_curves.deal_id.

A deal groups type curves that belong to the same acquisition / divestiture
package. Cardinality is 1:N — a curve belongs to at most one deal. The FK
is ON DELETE SET NULL so deleting a deal unassigns its curves rather than
cascade-deleting them (curves are expensive to rebuild; deals are cheap).

Revision ID: 0007_deals
Revises: 0006_forecasts_downtime_ratio
Create Date: 2026-05-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_deals"
down_revision: str | Sequence[str] | None = "0006_forecasts_downtime_ratio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "type_curves",
        sa.Column(
            "deal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deals.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_type_curves_deal_id", "type_curves", ["deal_id"])


def downgrade() -> None:
    op.drop_index("ix_type_curves_deal_id", table_name="type_curves")
    op.drop_column("type_curves", "deal_id")
    op.drop_table("deals")
