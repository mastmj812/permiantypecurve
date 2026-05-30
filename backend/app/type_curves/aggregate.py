"""Type curve aggregation.

For each post-peak month N and each stream (oil/gas/water), build the
distribution of normalized rates across all included wells and emit
P10/P25/P50/P75/P90 + arithmetic mean + well_count. Implied EUR per
1,000 lateral feet is the cumulative integral of each percentile's rate
series over the months we observed.

Normalization basis is per-1000-lateral-ft for v1; the function is
structured so per-proppant-lb or per-well can drop in via a different
`normalize_by` value without changing the aggregation math.

Alignment is peak-month: each well's t=0 is its own oil peak. Gas and
water inherit oil's peak (same rule as forecasting — streams need a
shared t=0 for coherent aggregation). Callers pass the peak_index per
well.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

# Standard percentiles surfaced everywhere downstream (chart bands,
# implied EUR table, CSV export).
PERCENTILES: tuple[int, ...] = (10, 25, 50, 75, 90)
PERCENTILE_KEYS: tuple[str, ...] = tuple(f"p{p}" for p in PERCENTILES)

# Mean number of days per calendar month — used to convert daily rates to
# per-month volumes when computing implied EUR. Matches DAYS_PER_YEAR/12.
DAYS_PER_MONTH: float = 365.0 / 12.0

NormalizationBasis = Literal["per_lateral_ft", "per_proppant_lb", "per_well"]
AlignmentMethod = Literal["peak_month", "first_prod_month"]


@dataclass(frozen=True)
class WellSeries:
    """One well's contribution to the aggregation: rate series from peak
    month forward (one per stream) + the normalization denominator."""

    api10: str
    lateral_ft: float | None
    proppant_lbs: float | None
    # rates are calday BOPD/MCFD/BWPD from peak month forward
    oil_rates: list[float | None]
    gas_rates: list[float | None]
    water_rates: list[float | None]


@dataclass(frozen=True)
class StreamSeries:
    months: int  # n post-peak months
    p10: list[float | None]
    p25: list[float | None]
    p50: list[float | None]
    p75: list[float | None]
    p90: list[float | None]
    mean: list[float | None]
    well_count: list[int]
    implied_eur_per_1000ft: dict[str, float]  # keys: p10..p90, mean
    # Fitted Arps decline to the P50 series. None when there isn't
    # enough data to fit. Holds qi/Di/b/Df, EUR per normalization unit,
    # R², and the model-evaluated rate at each input month for the
    # frontend smooth-curve overlay.
    fitted: dict[str, Any] | None = None
    # 50-year projected EUR per percentile + mean from independent Arps
    # fits to each series. Same keys as implied_eur_per_1000ft. Values
    # are None for series that couldn't be fit (too short / all-zero).
    # The display table reads from here; implied_eur_per_1000ft (the
    # data-window sum) is kept for diagnostics + backward-compat.
    fitted_eur_per_unit: dict[str, float | None] | None = None
    # Full per-percentile fit parameters (qi/Di/b/Df/qo/peak_index/...).
    # Same keys as fitted_eur_per_unit. Lets the CSV export re-evaluate
    # each percentile out to 600 months for economics-model handoff. Old
    # saved curves serialize this as None; the export falls back to
    # refitting from the raw per-month series in that case.
    fitted_per_percentile: dict[str, dict[str, Any] | None] | None = None


@dataclass(frozen=True)
class TypeCurveAggregate:
    n_months: int
    n_wells: int
    normalization_basis: NormalizationBasis
    alignment_method: AlignmentMethod
    streams: dict[str, StreamSeries]  # "oil", "gas", "water"


def _normalizer(w: WellSeries, basis: NormalizationBasis) -> float | None:
    """Returns the denominator (per 1000 ft, per million lbs, or 1.0).

    None means this well can't be normalized on the chosen basis (e.g.
    missing lateral). Aggregation skips it.
    """
    if basis == "per_lateral_ft":
        if w.lateral_ft is None or w.lateral_ft <= 0:
            return None
        return w.lateral_ft / 1000.0  # → rates expressed as BOPD per 1000 ft
    if basis == "per_proppant_lb":
        if w.proppant_lbs is None or w.proppant_lbs <= 0:
            return None
        return w.proppant_lbs / 1_000_000.0  # per million lbs
    return 1.0  # per_well: no normalization


def _stream_panel(
    rates_per_well: list[list[float | None]],
    denominators: list[float],
    n_months: int,
) -> StreamSeries:
    """Compute percentiles + mean + well_count + implied EUR for one stream.

    Each well's rate at month i is normalized by its denominator. For each
    month, we percentile across all wells that have a non-null normalized
    value there.
    """
    p10, p25, p50, p75, p90 = [], [], [], [], []  # type: ignore[var-annotated]
    means: list[float | None] = []
    well_counts: list[int] = []

    # Stack into a (n_wells, n_months) numpy array, NaN-filled where missing.
    arr = np.full((len(rates_per_well), n_months), np.nan, dtype=float)
    for i, well_rates in enumerate(rates_per_well):
        for j, v in enumerate(well_rates[:n_months]):
            if v is not None and np.isfinite(v):
                arr[i, j] = v / denominators[i]

    for j in range(n_months):
        col = arr[:, j]
        usable = col[~np.isnan(col)]
        well_counts.append(int(usable.size))
        if usable.size == 0:
            p10.append(None); p25.append(None); p50.append(None)
            p75.append(None); p90.append(None); means.append(None)
            continue
        # `linear` interpolation for percentiles is the standard.
        qs = np.percentile(usable, PERCENTILES, method="linear")
        p10.append(float(qs[0])); p25.append(float(qs[1]))
        p50.append(float(qs[2])); p75.append(float(qs[3])); p90.append(float(qs[4]))
        means.append(float(np.mean(usable)))

    series = {
        "p10": p10, "p25": p25, "p50": p50, "p75": p75, "p90": p90, "mean": means,
    }

    # Implied EUR per 1000 ft (or per other normalization unit) per
    # percentile. Sum rate × days_per_month over months we observed.
    # `None`s contribute 0 — wells dropping out reduces well_count but
    # doesn't shrink the integral for the percentile we still have.
    implied_eur: dict[str, float] = {}
    for key, vals in series.items():
        total = 0.0
        for v in vals:
            if v is not None and np.isfinite(v):
                total += v * DAYS_PER_MONTH
        implied_eur[key] = total

    return StreamSeries(
        months=n_months,
        p10=p10, p25=p25, p50=p50, p75=p75, p90=p90,
        mean=means,
        well_count=well_counts,
        implied_eur_per_1000ft=implied_eur,
    )


def aggregate(
    wells: list[WellSeries],
    *,
    normalization_basis: NormalizationBasis = "per_lateral_ft",
    alignment_method: AlignmentMethod = "peak_month",
    n_months: int | None = None,
) -> TypeCurveAggregate:
    """Build a TypeCurveAggregate from the included wells' rate series.

    `n_months` caps the output length. If None, uses the longest history
    present in any well. Per-month well_count tells the caller how many
    wells contribute at each tail position — typically tapers as older
    wells drop off.
    """
    if not wells:
        empty = StreamSeries(
            months=0, p10=[], p25=[], p50=[], p75=[], p90=[], mean=[],
            well_count=[], implied_eur_per_1000ft={k: 0.0 for k in PERCENTILE_KEYS + ("mean",)},
        )
        return TypeCurveAggregate(
            n_months=0, n_wells=0,
            normalization_basis=normalization_basis,
            alignment_method=alignment_method,
            streams={"oil": empty, "gas": empty, "water": empty},
        )

    usable_wells: list[WellSeries] = []
    denoms: list[float] = []
    for w in wells:
        d = _normalizer(w, normalization_basis)
        if d is None:
            continue
        usable_wells.append(w)
        denoms.append(d)

    if not usable_wells:
        empty = StreamSeries(
            months=0, p10=[], p25=[], p50=[], p75=[], p90=[], mean=[],
            well_count=[], implied_eur_per_1000ft={k: 0.0 for k in PERCENTILE_KEYS + ("mean",)},
        )
        return TypeCurveAggregate(
            n_months=0, n_wells=0,
            normalization_basis=normalization_basis,
            alignment_method=alignment_method,
            streams={"oil": empty, "gas": empty, "water": empty},
        )

    if n_months is None:
        n_months = max(len(w.oil_rates) for w in usable_wells)

    streams = {
        "oil": _stream_panel([w.oil_rates for w in usable_wells], denoms, n_months),
        "gas": _stream_panel([w.gas_rates for w in usable_wells], denoms, n_months),
        "water": _stream_panel([w.water_rates for w in usable_wells], denoms, n_months),
    }
    return TypeCurveAggregate(
        n_months=n_months,
        n_wells=len(usable_wells),
        normalization_basis=normalization_basis,
        alignment_method=alignment_method,
        streams=streams,
    )


def serialize_aggregate(agg: TypeCurveAggregate) -> dict:
    """JSONable shape for the API response and the saved `series` column."""
    return {
        "n_months": agg.n_months,
        "n_wells": agg.n_wells,
        "normalization_basis": agg.normalization_basis,
        "alignment_method": agg.alignment_method,
        "streams": {
            s: {
                "p10": v.p10, "p25": v.p25, "p50": v.p50,
                "p75": v.p75, "p90": v.p90,
                "mean": v.mean,
                "well_count": v.well_count,
                "implied_eur_per_1000ft": v.implied_eur_per_1000ft,
                "fitted": v.fitted,
                "fitted_eur_per_unit": v.fitted_eur_per_unit,
                "fitted_per_percentile": v.fitted_per_percentile,
            }
            for s, v in agg.streams.items()
        },
    }
