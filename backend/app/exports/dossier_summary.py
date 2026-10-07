"""Per-zone dossier summary — the deck's opening "what takes what" slide.

One row per Blue Ox zone: the planned sticks routed to it (same routing
as the drop, ``_fetch_narvi_by_zone``), the type curve they take, the
cohort that builds it, and the TC-vs-Novi read. Ports the deal-intake
dossier's curve summary (engineering_db ``dealintake/render``) onto the
Blue Ox drop config.

Pure module: callers hand in the TypeCurve, the zone's narvi wells, the
zone's Novi comparison and the cohort's global forecast rows; nothing
here touches a DB or the warehouse.

Conventions (stated on the slide):
  * TC = the published P50 fit (``series.streams.<s>.fitted``) with the
    curve's geologic risking applied — the same numbers the deck's
    param table and the drop deliver.
  * EUR per 1,000 ft of completed lateral, raw 50-yr technical integral
    (no economic limit).
  * Novi = the zone's Novi comparison series: per-1,000-ft median of the
    representative sticks, 600 x 30-day periods (~49.3 yr), trapezoid-
    integrated like the comparison figure's cum panel. A
    median-series EUR, not a P50 and not erebor's cohort mean.
  * Di is NOMINAL per year with the 1-yr effective decline beside it.
  * The TC/Novi gap flag fires beyond 1.5x either way — the deal-intake
    threshold (``qc_flags.stream_gap_flag_ratio``). A flag, never a gate.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.db.models import Forecast, TypeCurve
from app.forecasting.fit import detect_at_bound
from app.type_curves.risking import normalize_multipliers, risked_name_suffix

GAP_FLAG_RATIO = 1.5
SUMMARY_STREAMS = ("oil", "gas")


@dataclass(frozen=True)
class StreamSummary:
    eur_per_1000ft: float | None  # risked P50, raw 50-yr
    qi_per_1000ft: float | None  # risked P50 qi (cal-day rate per 1,000 ft)
    di_nominal: float | None  # per year
    di_effective: float | None  # 1-yr secant effective, 0-1
    b: float | None
    risk_mult: float
    novi_eur_per_1000ft: float | None
    tc_vs_novi: float | None  # TC / Novi - 1
    gap_flag: bool


@dataclass(frozen=True)
class CohortQC:
    n_wells: int
    n_overridden: int  # wells with a TC-scoped override on any stream
    at_bound: dict[str, int] = field(default_factory=dict)  # stream -> wells, any bound
    # Which parameter is pinned: b at 0.9/1.2 is the common case (the
    # window is deliberately narrow); Di pinned is the one to chase.
    di_at_bound: dict[str, int] = field(default_factory=dict)
    b_at_bound: dict[str, int] = field(default_factory=dict)
    missing: dict[str, int] = field(default_factory=dict)  # stream -> wells w/o a forecast


@dataclass(frozen=True)
class ZoneStick:
    """One planned stick routed to the zone (PDP context excluded)."""

    well_name: str
    formation: str | None
    category: str  # PUD | UPSIDE (handoff category)
    scenario_ref: str  # "<deal_id>/<scenario_id>"
    completed_lateral_ft: float | None
    target_tvd_ft: float | None
    legs_lonlat: tuple[tuple[float, float, float, float], ...] = ()


@dataclass(frozen=True)
class CohortWell:
    """One well that builds the zone's curve, for the support map: its
    lateral (wellstick, lon/lat) and anduin's own resolved oil EUR per
    ft (override -> global, raw 50-yr, UNRISKED — the /well-stats
    number the probit dots use)."""

    api10: str
    name: str | None
    lateral_ft: float | None
    oil_eur_per_ft: float | None
    coords: tuple[tuple[float, float], ...] = ()


def parse_linestring_wkt(wkt: str | None) -> tuple[tuple[float, float], ...]:
    """``LINESTRING(x y, x y, ...)`` -> ((x, y), ...); () when absent or
    not a plain linestring (the wellstick column is always one)."""
    if not wkt:
        return ()
    head, _, body = wkt.partition("(")
    if head.strip().upper() != "LINESTRING" or not body.endswith(")"):
        return ()
    out = []
    for pair in body[:-1].split(","):
        parts = pair.split()
        if len(parts) >= 2:
            out.append((float(parts[0]), float(parts[1])))
    return tuple(out)


@dataclass(frozen=True)
class ZoneSummary:
    zone_name: str
    type_curve_id: str
    curve_name: str  # with the risked suffix, as the deck titles it
    reserve_category: str  # the zone's declared category
    benches: tuple[str, ...]
    sticks: tuple[ZoneStick, ...]
    n_pud: int
    n_upside: int
    n_scenarios: int
    planned_lateral_ft_median: float | None
    streams: dict[str, StreamSummary]
    qc: CohortQC
    novi_n_sticks: int
    novi_low_n: bool
    novi_stale: bool
    novi_error: str | None = None
    cohort: tuple[CohortWell, ...] = ()

    @property
    def n_sticks(self) -> int:
        return len(self.sticks)

    @property
    def flags(self) -> list[str]:
        out = [
            f"{s}: TC {v.tc_vs_novi + 1:.2f}x Novi (beyond {GAP_FLAG_RATIO:g}x)"
            for s, v in self.streams.items()
            if v.gap_flag and v.tc_vs_novi is not None
        ]
        if self.novi_low_n:
            out.append(f"Novi LOW N ({self.novi_n_sticks} sticks)")
        if self.novi_stale:
            out.append("Novi STALE VINTAGE")
        if self.qc.di_at_bound.get("oil"):
            out.append(f"{self.qc.di_at_bound['oil']} cohort oil fits with Di at a bound")
        return out


def one_yr_effective(di_nominal: float | None, b: float | None) -> float | None:
    """1-yr secant effective from nominal Di (/yr) and Arps b — same
    formula as the drop's curve_params note (``_one_yr_effective``)."""
    if di_nominal is None or di_nominal <= 0:
        return None
    try:
        if b is not None and b > 0:
            return 1.0 - float((1.0 + b * di_nominal) ** (-1.0 / b))
        return 1.0 - math.exp(-di_nominal)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def novi_eur_per_1000ft(period_volumes: Sequence[float]) -> float | None:
    """EUR of the zone's Novi median series (per 1,000 ft): trapezoid over
    the 30-day periods, anchored at t=0, last period held flat — exactly
    the comparison figure's ``cumFrom`` so the summary and the figure's
    end-point label read the same number."""
    v = [float(x) if math.isfinite(float(x)) else 0.0 for x in period_volumes]
    total = sum((a + (v[i + 1] if i + 1 < len(v) else a)) / 2.0 for i, a in enumerate(v))
    return total if total > 0 else None


def stream_summary(tc: TypeCurve, stream: str, novi_eur: float | None) -> StreamSummary:
    fitted = ((tc.series or {}).get("streams") or {}).get(stream, {}).get("fitted") or {}
    mul = normalize_multipliers(tc.risk_multipliers or {})[stream]
    eur = _num(fitted.get("eur_per_unit"))
    qi = _num(fitted.get("qi"))
    eur = eur * mul if eur is not None else None
    qi = qi * mul if qi is not None else None
    di = _num(fitted.get("Di"))
    b = _num(fitted.get("b"))
    ratio = eur / novi_eur - 1.0 if eur and novi_eur else None
    gap = ratio is not None and (ratio + 1.0 > GAP_FLAG_RATIO or ratio + 1.0 < 1.0 / GAP_FLAG_RATIO)
    return StreamSummary(
        eur_per_1000ft=eur,
        qi_per_1000ft=qi,
        di_nominal=di,
        di_effective=one_yr_effective(di, b),
        b=b,
        risk_mult=mul,
        novi_eur_per_1000ft=novi_eur,
        tc_vs_novi=ratio,
        gap_flag=gap,
    )


def cohort_qc(
    tc: TypeCurve,
    forecasts: Mapping[str, Mapping[str, Forecast]],
    di_hi: Mapping[str, float],
) -> CohortQC:
    """Overrides + at-bound + missing counts over the curve's cohort.

    Resolution mirrors ``resolve_forecast``: a TC-scoped override wins
    over the global row. At-bound uses ``detect_at_bound`` with each
    stream's own Di cap (the Review badge rule).
    """
    api10s = list(tc.included_api10s or [])
    overrides = tc.forecast_overrides or {}
    n_over = sum(1 for a in api10s if any((overrides.get(a) or {}).values()))
    at_bound: dict[str, int] = {}
    di_bound: dict[str, int] = {}
    b_bound: dict[str, int] = {}
    missing: dict[str, int] = {}
    for stream in SUMMARY_STREAMS:
        nb = nm = ndi = nbb = 0
        for a in api10s:
            payload = (overrides.get(a) or {}).get(stream)
            if payload:
                qi, di, b, peak = (
                    payload.get("qi"),
                    payload.get("di_initial"),
                    payload.get("b"),
                    payload.get("peak_rate"),
                )
            else:
                f = forecasts.get(stream, {}).get(a)
                if f is None:
                    nm += 1
                    continue
                qi, di, b, peak = f.qi, f.di_initial, f.b, f.peak_rate
            if qi is None or di is None or peak is None:
                continue
            hit, note = detect_at_bound(
                qi=float(qi),
                di=float(di),
                b=None if b is None else float(b),
                peak_rate=float(peak),
                di_hi=di_hi[stream],
            )
            nb += int(hit)
            ndi += int("Di at" in (note or ""))
            nbb += int("b at" in (note or ""))
        at_bound[stream] = nb
        di_bound[stream] = ndi
        b_bound[stream] = nbb
        missing[stream] = nm
    return CohortQC(
        n_wells=len(api10s),
        n_overridden=n_over,
        at_bound=at_bound,
        di_at_bound=di_bound,
        b_at_bound=b_bound,
        missing=missing,
    )


def build_zone_summary(
    *,
    zone_name: str,
    reserve_category: str,
    benches: Sequence[str],
    tc: TypeCurve,
    sticks: Sequence[ZoneStick],
    novi_volumes: Mapping[str, Sequence[float]] | None,
    novi_n_sticks: int,
    novi_low_n: bool,
    novi_stale: bool,
    qc: CohortQC,
    novi_error: str | None = None,
    cohort: Sequence[CohortWell] = (),
) -> ZoneSummary:
    streams = {
        s: stream_summary(tc, s, novi_eur_per_1000ft((novi_volumes or {}).get(s, ())))
        for s in SUMMARY_STREAMS
    }
    laterals = [s.completed_lateral_ft for s in sticks if s.completed_lateral_ft]
    return ZoneSummary(
        zone_name=zone_name,
        type_curve_id=str(tc.id),
        curve_name=f"{tc.name}{risked_name_suffix(tc.risk_multipliers or {})}",
        reserve_category=reserve_category,
        benches=tuple(benches),
        sticks=tuple(sticks),
        n_pud=sum(1 for s in sticks if s.category == "PUD"),
        n_upside=sum(1 for s in sticks if s.category != "PUD"),
        n_scenarios=len({s.scenario_ref for s in sticks}),
        planned_lateral_ft_median=statistics.median(laterals) if laterals else None,
        streams=streams,
        qc=qc,
        novi_n_sticks=novi_n_sticks,
        novi_low_n=novi_low_n,
        novi_stale=novi_stale,
        novi_error=novi_error,
        cohort=tuple(cohort),
    )


def _f(v: float | None, fmt: str = ",.0f") -> str:
    return "—" if v is None else format(v, fmt)


def _di(s: StreamSummary) -> str:
    if s.di_nominal is None:
        return "—"
    eff = f" ({s.di_effective:.0%})" if s.di_effective is not None else ""
    return f"{s.di_nominal:.2f}{eff}"


def _vs(s: StreamSummary) -> str:
    if s.tc_vs_novi is None:
        return "—"
    return f"{s.tc_vs_novi:+.0%}" + (" ⚑" if s.gap_flag else "")


SUMMARY_HEADERS: tuple[str, ...] = (
    "Zone",
    "Type curve",
    "Sticks (PUD / UPSIDE)",
    "Planned lateral ft",
    "Cohort wells",
    "Oil EUR /1,000 ft",
    "Novi oil",
    "Oil TC vs Novi",
    "Oil Di nom /yr (1-yr eff)",
    "Oil b",
    "Gas EUR /1,000 ft",
    "Novi gas",
    "Gas TC vs Novi",
    "Risk",
    "QC",
)


def summary_cells(z: ZoneSummary) -> tuple[str, ...]:
    """One table row, matching ``SUMMARY_HEADERS``. The frontend preview
    renders the same strings (they arrive pre-formatted in the API) so
    the preview and the deck can't drift."""
    o, g = z.streams["oil"], z.streams["gas"]
    risk = (
        "—"
        if o.risk_mult == 1.0 and g.risk_mult == 1.0
        else (
            f"×{o.risk_mult:g}"
            if o.risk_mult == g.risk_mult
            else f"oil ×{o.risk_mult:g} / gas ×{g.risk_mult:g}"
        )
    )
    qc_bits = []
    for st in SUMMARY_STREAMS:
        di_n, b_n = z.qc.di_at_bound.get(st, 0), z.qc.b_at_bound.get(st, 0)
        if di_n or b_n:
            qc_bits.append(f"{st} pinned: Di {di_n} · b {b_n}")
    if z.qc.n_overridden:
        qc_bits.append(f"{z.qc.n_overridden} overridden")
    if z.qc.missing.get("oil"):
        qc_bits.append(f"{z.qc.missing['oil']} no oil fit")
    if z.novi_low_n:
        qc_bits.append(f"Novi low n ({z.novi_n_sticks})")
    if z.novi_stale:
        qc_bits.append("Novi stale vintage")
    if z.novi_error:
        qc_bits.append("Novi unavailable")
    return (
        z.zone_name,
        z.curve_name,
        f"{z.n_sticks} ({z.n_pud} / {z.n_upside})",
        _f(z.planned_lateral_ft_median),
        str(z.qc.n_wells),
        _f(o.eur_per_1000ft),
        _f(o.novi_eur_per_1000ft),
        _vs(o),
        _di(o),
        _f(o.b, ".2f"),
        _f(g.eur_per_1000ft),
        _f(g.novi_eur_per_1000ft),
        _vs(g),
        risk,
        "; ".join(qc_bits) or "—",
    )


SUMMARY_NOTE = (
    "Per 1,000 ft of completed lateral. TC = published P50 fit, risked as delivered; EUR = raw 50-yr technical "
    "integral (no economic limit). Novi = median of each zone's representative sticks (per-1,000-ft median "
    "series, 600 x 30-day periods, trapezoid as on the comparison figure) — not a P50. Di = nominal /yr with 1-yr effective in brackets. "
    f"⚑ = TC and Novi more than {GAP_FLAG_RATIO:g}x apart (flag, not a gate). Sticks = planned narvi wells "
    "routed to the zone; PDP context excluded. No economics."
)
