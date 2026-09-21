"""Bench-level b priors for short-history fits.

Why this exists: with b actually free in the cum fit (it was frozen at
1.00 until the ``cum_hyperbolic`` fix), a well with a short post-peak
history cannot determine b — the estimator's spread is as wide as the
[0.9, 1.2] bounds, so b lands on a bound and the row is flagged. The fit
therefore pulls b toward a bench prior, with a weight that tapers to zero
as history accumulates (see ``prior_weight`` and
``fit._fit_with_b_prior``).

Prior source: a POOLED bench fit, not a median of per-well b values
(~80% of long-history per-well fits sit on a b bound, so their median is
just 0.9 or 1.2). For each (sub-basin, formation_blueox, stream),
long-history wells are normalized by their own peak rate, aligned at the
peak, reduced to a cross-well median decline curve, and that one curve is
fit with the production fitter. ``app.cli.build_b_priors`` writes the
result to ``data/b_priors.json``, which is versioned in the repo so fits
stay pure and DB-free.

Lookup order: bench -> sub-basin -> DEFAULT_B_PRIOR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PRIORS_PATH = Path(__file__).parent / "data" / "b_priors.json"

# Same NULL key the rest of the stack uses for formation_blueox grouping.
UNMAPPED = "(unmapped)"
# The fit's own starting point (fit.B_P0) — used when no cohort qualifies.
DEFAULT_B_PRIOR: float = 1.0

# Builder thresholds (kept here so the JSON's meaning is defined in one place).
MIN_FIT_MONTHS: int = 36  # a well needs this many post-peak fit months to lend
MIN_WELLS: int = 15  # a cohort needs this many lenders to get its own prior
MIN_MONTH_COVERAGE: float = 0.5  # month kept while >= this share of lenders report
# Lenders must look like the wells being forecast. anduin's production
# table holds the whole synced universe (~62k wells, 17% pre-2016), while
# forecasted wells are 99% 2016+ vintage with >= 7,000 ft laterals. Older
# completions decline differently (Delaware BS3_S pooled b: 1.20 all
# vintages vs 1.11 for 2016+).
MIN_LATERAL_FT: float = 3000.0
MIN_VINTAGE_YEAR: int = 2016


@dataclass(frozen=True)
class BPrior:
    b: float
    source: str  # "bench" | "subbasin" | "default"
    key: str  # e.g. "Delaware|WCA_1", "Midland", "default"
    n_wells: int


def bench_key(subbasin: str | None, formation_blueox: str | None) -> str:
    return f"{subbasin or UNMAPPED}|{formation_blueox or UNMAPPED}"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not PRIORS_PATH.exists():
        return {"bench": {}, "subbasin": {}}
    with PRIORS_PATH.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def lookup_b_prior(subbasin: str | None, formation_blueox: str | None, stream: str) -> BPrior:
    data = _load()
    for source, table, key in (
        ("bench", data.get("bench", {}), bench_key(subbasin, formation_blueox)),
        ("subbasin", data.get("subbasin", {}), subbasin or UNMAPPED),
    ):
        entry = (table.get(key) or {}).get(stream)
        if entry and entry.get("b") is not None:
            return BPrior(
                b=float(entry["b"]), source=source, key=key, n_wells=int(entry["n_wells"])
            )
    return BPrior(b=DEFAULT_B_PRIOR, source="default", key="default", n_wells=0)


def prior_weight(n_fit_months: int, *, full_weight_months: int, zero_weight_months: int) -> float:
    """1.0 at <= full_weight_months, linear to 0.0 at >= zero_weight_months."""
    if n_fit_months >= zero_weight_months:
        return 0.0
    if n_fit_months <= full_weight_months:
        return 1.0
    return (zero_weight_months - n_fit_months) / (zero_weight_months - full_weight_months)


def pooled_decline_curve(
    normalized: list[dict[int, float]],
    *,
    min_coverage: float = MIN_MONTH_COVERAGE,
) -> list[float]:
    """Cross-well median of peak-normalized rates by months-since-peak.

    Each element maps month index k (0 = peak month) -> rate / peak_rate
    for one lender; downtime months are simply absent. The curve stops at
    the first month reported by fewer than ``min_coverage`` of the lenders
    so the tail isn't set by the few oldest wells (survivorship).
    """
    lenders = [w for w in normalized if w]
    if not lenders:
        return []
    need = max(1.0, min_coverage * len(lenders))
    out: list[float] = []
    for k in range(max(max(w) for w in lenders) + 1):
        vals = [w[k] for w in lenders if k in w]
        if len(vals) < need:
            break
        out.append(float(np.median(vals)))
    return out


def fit_pooled_b(
    curve: list[float], *, stream: str, df_terminal_per_year: float
) -> dict[str, Any] | None:
    """Fit the pooled curve with the PRODUCTION fitter — same b bounds, same
    peak anchor, and the STREAM's own Di cap (water 12.0/yr, oil/gas 4.0/yr)
    — and return its b. None when the curve is too short."""
    # Local imports: fit.py must stay importable without this module.
    from app.forecasting.eur import DAYS_PER_YEAR
    from app.forecasting.fit import (
        B_HI,
        B_LO,
        BOUND_TOLERANCE_PCT,
        STREAM_RATE_COLUMN,
        STREAM_VOLUME_COLUMN,
        fit_rate_cum,
    )
    from app.forecasting.peak_detection import PeakResult
    from app.forecasting.types import ForecastConfig

    if len(curve) < MIN_FIT_MONTHS:
        return None
    # Scale the unit-peak curve to a realistic rate so the absolute
    # downtime floor (5 BOPD etc.) never bites; b and Di are scale-free.
    scale = 1000.0
    dates = list(pd.date_range("2020-01-01", periods=len(curve), freq="MS").date)
    rates = [scale * v for v in curve]
    frame = pd.DataFrame(
        {
            "prod_date": dates,
            STREAM_RATE_COLUMN[stream]: rates,
            STREAM_VOLUME_COLUMN[stream]: [r * DAYS_PER_YEAR / 12.0 for r in rates],
        }
    )
    peak = PeakResult(peak_month_date=dates[0], peak_rate=rates[0], peak_index=0)
    cfg = ForecastConfig(df_terminal_per_year=df_terminal_per_year)
    r = fit_rate_cum(frame, model_type="modified_hyperbolic", peak=peak, stream=stream, config=cfg)
    if r.b is None or r.di_initial is None:
        return None
    return {
        "b": round(float(r.b), 4),
        "Di": round(float(r.di_initial), 4),
        "r2": round(float(r.fit_r2), 5),
        "n_months": len(curve),
        "b_at_bound": bool(r.b < B_LO + BOUND_TOLERANCE_PCT or r.b > B_HI - BOUND_TOLERANCE_PCT),
    }
