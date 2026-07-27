"""type_curves.risk_multipliers — geologic risking scalars.

Per-stream scalar multipliers ``{oil?: float, gas?: float, water?: float}``
applied to a curve's delivered volumes after geologic review. An absent
key means 1.0 (unrisked). The stored ``series`` stays the CLEAN technical
fit — risking is applied at the read/export boundary (see
app/type_curves/risking.py), so re-aggregation never loses or
double-applies the multiplier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0025_type_curve_risk_multipliers"
down_revision: str | Sequence[str] | None = "0024_deal_blueox_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "type_curves",
        sa.Column(
            "risk_multipliers",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("type_curves", "risk_multipliers")
