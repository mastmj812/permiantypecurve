"""deals.blueox_config — persisted Blue Ox curve-drop configuration.

One anduin deal = one Blue Ox deal = one governing workbook. The config
is the assignment surface: kickoff params (codename / curve_months /
levels / prepared_by), the zone list (type curves — cross-deal allowed —
with zone names, reserve categories and narvi bench maps), and the
narvi scenario selections with pinned updated_at stamps for staleness
detection. Stored as the JSON dump of the BlueOxExportRequest model so
config and ad-hoc export payloads can never drift apart.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0024_deal_blueox_config"
down_revision: str | Sequence[str] | None = "0023_wells_formation_blueox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deals", sa.Column("blueox_config", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("deals", "blueox_config")
