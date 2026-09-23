"""wells.* development-scenario passthrough from curated.dev_scenario.

engineering_db sql/50 (``curated.dev_scenario``, a plain view over
``curated.codev_context``) classifies every producing horizontal by what
was already producing above/below it at first production:
sandwich > topfill > underfill > codev_stack > standalone. A vertical
parent = other mapped bench, online > 180 d before the subject, closest
parent lateral midpoint <= 660 ft from the subject lateral, |dTVD| <=
1,000 ft; a side is SHIELDED (doesn't count) when a co-developed well sits
between subject and parent. The class is parent-side, fully observed at
first production — not censored. ``child_censored`` flags the child side
only.

``scenario_bench`` is the warehouse's TVD-corrected bench (codev_context
key) — NOT the same column as ``formation_blueox`` (raw Blue Ox mapping);
the two differ where the TVD correction moved a well. The scenario filter
keys on ``scenario_bench`` so a map query and scripts/find_analogs.py
return the same wells.

``scenario_bench_context`` is the per-bench jsonb (codev_context
.bench_context): the bench-pair parent filter ("WCA_1 beneath LSSH") reads
it directly — no vertical window, no shielding — exactly like
find_analogs --parent-bench.

Existing rows stay NULL until the next wells sync repopulates.

Revision ID: 0032_well_dev_scenario
Revises: 0031_fit_method_bprior
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032_well_dev_scenario"
down_revision: str | Sequence[str] | None = "0031_fit_method_bprior"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEXT_ARRAY = postgresql.ARRAY(sa.Text())

_COLUMNS: list[sa.Column[Any]] = [
    sa.Column("scenario_bench", sa.String(64), nullable=True),
    sa.Column("scenario_class", sa.String(16), nullable=True),
    sa.Column("parent_benches_below", _TEXT_ARRAY, nullable=True),
    sa.Column("parent_benches_above", _TEXT_ARRAY, nullable=True),
    sa.Column("nearest_parent_below_dtvd_ft", sa.Float(), nullable=True),
    sa.Column("nearest_parent_above_dtvd_ft", sa.Float(), nullable=True),
    sa.Column("shielded_below", sa.Boolean(), nullable=True),
    sa.Column("shielded_above", sa.Boolean(), nullable=True),
    sa.Column("nearest_parent_offset_ft", sa.Float(), nullable=True),
    sa.Column("youngest_parent_age_days", sa.Integer(), nullable=True),
    sa.Column("oldest_parent_age_days", sa.Integer(), nullable=True),
    sa.Column("has_same_bench_parent", sa.Boolean(), nullable=True),
    sa.Column("codev_benches_other", _TEXT_ARRAY, nullable=True),
    sa.Column("child_benches_other", _TEXT_ARRAY, nullable=True),
    sa.Column("child_censored", sa.Boolean(), nullable=True),
    sa.Column("scenario_bench_context", postgresql.JSONB(), nullable=True),
]


def upgrade() -> None:
    for col in _COLUMNS:
        op.add_column("wells", col)
    # Filter predicates on the map tile / selection / facet queries.
    op.create_index("ix_wells_scenario_class", "wells", ["scenario_class"])
    op.create_index("ix_wells_scenario_bench", "wells", ["scenario_bench"])


def downgrade() -> None:
    op.drop_index("ix_wells_scenario_bench", table_name="wells")
    op.drop_index("ix_wells_scenario_class", table_name="wells")
    for col in reversed(_COLUMNS):
        op.drop_column("wells", col.name)
