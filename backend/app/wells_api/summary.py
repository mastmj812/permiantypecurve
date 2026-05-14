"""Selection summary builder — pure function over well rows.

Stats (per the brief):
  * count
  * median lateral length
  * vintage histogram (year → count)
  * operator breakdown (operator → count, top 5)

Kept DB-free so the selection-endpoint test can verify behavior against
hand-built rosters.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass

# Selection caps per the brief: hard 500, soft warning at 300.
HARD_SELECTION_CAP = 500
SOFT_SELECTION_WARNING = 300


@dataclass(frozen=True)
class WellSummaryRow:
    api14: str
    formation: str | None
    operator: str | None
    first_prod_year: int | None
    lateral_ft: float | None


@dataclass(frozen=True)
class SelectionSummary:
    count: int
    median_lateral_ft: float | None
    vintage_histogram: dict[int, int]
    operators_top5: list[tuple[str, int]]
    formations: dict[str, int]
    exceeds_soft_cap: bool
    exceeds_hard_cap: bool


def build_summary(rows: list[WellSummaryRow]) -> SelectionSummary:
    count = len(rows)
    laterals = [r.lateral_ft for r in rows if r.lateral_ft is not None and r.lateral_ft > 0]
    median = statistics.median(laterals) if laterals else None

    vintage = Counter(r.first_prod_year for r in rows if r.first_prod_year is not None)
    operators = Counter(r.operator for r in rows if r.operator)
    formations = Counter(r.formation for r in rows if r.formation)

    return SelectionSummary(
        count=count,
        median_lateral_ft=median,
        vintage_histogram=dict(sorted(vintage.items())),
        # `most_common(5)` returns [(name, n), ...] in descending order.
        operators_top5=list(operators.most_common(5)),
        formations=dict(formations.most_common()),
        exceeds_soft_cap=count > SOFT_SELECTION_WARNING,
        exceeds_hard_cap=count > HARD_SELECTION_CAP,
    )
