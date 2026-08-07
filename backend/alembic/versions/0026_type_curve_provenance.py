"""type_curves.provenance — type-well build-up traceability.

Raw funnel inputs recorded at save time so the "type well build-up"
(starting universe by formation → culls with reasons → final cohort)
can be computed and exported later: AOI polygons, filter snapshot,
selection events, universe snapshot, forecast partition, coded review
exclusions, and post-save membership changes. The cull waterfall itself
is never stored — it is derived read-side (app/type_curves/buildup.py)
so there is exactly one source of truth.

Empty ``{}`` means "provenance not recorded" (curve saved before this
migration); exports degrade gracefully.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0026_type_curve_provenance"
down_revision: str | Sequence[str] | None = "0025_type_curve_risk_multipliers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "type_curves",
        sa.Column(
            "provenance",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("type_curves", "provenance")
