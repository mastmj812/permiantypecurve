"""blueox_exports — log of Blue Ox drop exports + #eng-updates notices.

One row per successful curve-drop export; ``notified_*`` stamped when
the "Notify Blue Ox" button posts the drop notice. See
app.db.models.blueox_exports.

Revision ID: 0034_blueox_exports
Revises: 0033_pdp_daily_forecasts
Create Date: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0034_blueox_exports"
down_revision: str | Sequence[str] | None = "0033_pdp_daily_forecasts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blueox_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deals.id", ondelete="CASCADE", name="fk_blueox_exports_deal_id_deals"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("export_date", sa.Date(), nullable=False),
        sa.Column("allow_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.Column("notified_codename", sa.String(255)),
        sa.Column("notified_text", sa.Text()),
        sa.Column("notified_by", sa.String(255)),
    )
    op.create_index("ix_blueox_exports_deal_created", "blueox_exports", ["deal_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_blueox_exports_deal_created", table_name="blueox_exports")
    op.drop_table("blueox_exports")
