"""Panel alignment math for type-curve aggregation — no DB.

Split out of anduin's ``app.type_curves.loader`` (which keeps the DB
fetch and override resolution) so the BOX batch builds its per-well
panels with the identical mechanism.

``peak_ramp`` (alignment of record): every well's PEAK sits at a common
month index M = the cohort-median ramp length per stream
(:func:`ramp_anchor`), each well slid along the panel by
``M - peak_index_months`` (:func:`peak_ramp_shift`). Compute the anchors
ONCE per aggregation and hand the same dict to every loader (forecast
bands and observed overlay) or they desynchronize.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

from boxfit.ramp_arps import evaluate_well_rate


def ramp_anchor(ramps: Iterable[int]) -> int:
    """Common peak month index M for one stream: the rounded median of the
    cohort's ``peak_index_months`` (wells whose fit carries no ramp count
    as 0). The median guarantees at least one well's ramp reaches back to
    panel month 0, so the ramp region always has data. Empty → 0."""
    ramps = list(ramps)
    return round(float(np.median(ramps))) if ramps else 0


def peak_ramp_shift(
    params: Mapping[str, Any] | None, stream: str, anchors: Mapping[str, int]
) -> int:
    """Months to slide one well's stream so its peak lands on the anchor M
    (``M - peak_index_months``). 0 when the stream has no params."""
    if params is None:
        return 0
    m_w = int(params.get("peak_index_months") or 0)
    return int(anchors.get(stream, 0)) - m_w


def forecast_rates(
    params: Mapping[str, Any] | None,
    n_months: int,
    *,
    include_ramp: bool,
    shift_months: int = 0,
) -> list[float | None]:
    """N-month rate trajectory from t=0 forward.

    ``include_ramp`` controls whether the ramp prefix is evaluated:
    True under first_prod_month / peak_ramp alignment, False under
    peak_month (t=0 is peak, no ramp segment to draw). When ramp params
    are missing from `params`, evaluate_well_rate falls back to pure
    Arps regardless.

    ``shift_months`` slides the well along the panel's month axis —
    the peak_ramp mechanism. Positive: the well starts that many
    months late (front-padded with nulls — there's no production
    signal before its onset). Negative: the well's earliest ramp
    months fall before the panel and are dropped. The caller passes
    ``M - peak_index_months`` so every well's peak lands on the common
    month M.
    """
    if params is None or n_months <= 0:
        return [None] * max(n_months, 0)
    if params.get("mode") == "ratio":
        # Ratio rows are evaluated by the caller against the well's own
        # oil trajectory (see load_wells_with_forecast) — reaching this
        # generic Arps evaluator with one is a programming error; null
        # the stream rather than crash the whole aggregation.
        return [None] * n_months
    lead = max(0, shift_months)
    t_years = (np.arange(n_months - lead, dtype=float) - min(shift_months, 0)) / 12.0
    qo = params.get("qo") if include_ramp else None
    peak_index_months = params.get("peak_index_months") if include_ramp else None
    rates = evaluate_well_rate(
        qo=qo,
        peak_index_months=peak_index_months,
        qi=params["qi"],
        Di=params["Di"],
        b=params["b"],
        Df=params["Df"],
        t_years=t_years,
    )
    return [None] * lead + [float(x) for x in rates]
