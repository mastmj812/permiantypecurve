"""Median novi_intel ML forecast for representative stick sets.

The Blue Ox drop's TC-vs-Novi comparison (2026-07-27 amendment): every
zone ships the MEDIAN Novi Intelligence forecast of the representative
novi_intel sticks behind its captured narvi wells, per-1,000-ft
normalized, so the dossier's 6-panel comparison is reproducible from
the workbook alone.

Selection rule of record lives in the warehouse
(``curated.intel_representative_sticks``, engineering_db sql/35):
generated narvi wells take the neighborhood set (same formation_blueox
bench, PUD/RES sticks within 1 mi, lateral within ±25%); curated
pud/res pass-throughs ARE novi sticks and compare against their own
forecast. narvi persists the resolved set per well at scenario save
(``detail->novi_rep``); :func:`resolve_rep_set` reads that and falls
back to the same warehouse rule for legacy saves.

Series construction mirrors erebor's ``/production/aggregate`` stitch
(the reference implementation): monthly P50 forecast rows from
``curated.intel_forecast`` (per-day rates on a 30-day grid, ~30-yr
horizon) continued by the per-stream Arps tail from
``curated.intel_arps`` out to month :data:`N_MONTHS`. Rates normalize
per-1,000-ft by each stick's ``ll_ft`` BEFORE the per-month median
across sticks; volumes are rate x :data:`STEP_DAYS` (the Novi grid is
30-day periods — declared in the manifest as
``novi_rate_to_volume_days``).

Queries key on ``novi_wellname`` (= ``intel_locations.unique_id``) —
the indexed path through the intel views (sql/29 header note).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.warehouse_client.narvi import NarviInventoryWell

log = get_logger("warehouse_client.intel_forecast")

# Selection defaults — MUST mirror sql/35's defaults and narvi's
# NOVI_REP_RADIUS_M / NOVI_REP_LATERAL_TOL (the persisted sets and the
# fallback must select identically).
REP_RADIUS_M = 1609.0  # 1 mi
REP_LATERAL_TOL = 0.25
REP_LOW_N = 3

# Comparison series grid: Novi-native 30-day periods, 600 of them
# (~49.3 yr) — the tail beyond the ~30-yr monthly forecast comes from
# the intel Arps segments, exactly like erebor's aggregate endpoint.
N_MONTHS = 600
STEP_DAYS = 30

_STREAMS = ("oil", "gas", "water")


@dataclass(frozen=True)
class RepSet:
    """One well's resolved representative stick set."""

    mode: str  # "self" | "neighborhood"
    stick_ids: tuple[int, ...]
    low_n: bool
    intel_vintage: str | None  # vintage recorded at narvi save; None = fallback
    source: str  # "persisted" | "fallback"


_VINTAGE_SQL = text("SELECT curated.intel_vintage_date()::text")

_SELF_STICK_SQL = text(
    """
    SELECT stick_id FROM curated.intel_locations
    WHERE unique_id = :uid AND category IN ('PUD', 'RES')
    """
)

_REP_STICKS_SQL = text(
    """
    SELECT stick_id
    FROM curated.intel_representative_sticks(
        ST_GeomFromEWKT(:legs), :bench, :lateral, :radius, :tol)
    """
)

_STICK_META_SQL = text(
    """
    SELECT il.stick_id, il.unique_id, il.category, il.ll_ft, il.basin
    FROM curated.intel_locations il
    WHERE il.stick_id = ANY(:ids)
    """
)

_FORECAST_SQL = text(
    """
    SELECT novi_wellname, mop, oil, gas, water
    FROM curated.intel_forecast
    WHERE novi_wellname = ANY(:names)
    ORDER BY novi_wellname, mop
    """
)

_ARPS_SQL = text(
    """
    SELECT novi_wellname, production_stream, segment_curve_type,
           b, d_nom, q_start, day_start, day_stop
    FROM curated.intel_arps
    WHERE novi_wellname = ANY(:names)
    ORDER BY novi_wellname, production_stream, segment
    """
)


def fetch_intel_vintage(wh: Session) -> str | None:
    """Current warehouse intel vintage (quarter-end date as ISO text)."""
    row = wh.execute(_VINTAGE_SQL).scalar()
    return str(row) if row is not None else None


def _bench_code(formation: str) -> str:
    """narvi's wine-rack bimodal suffix stripped (WCA_1_b -> WCA_1)."""
    return formation[:-2] if formation.endswith("_b") else formation


def _legs_ewkt(quads: tuple[tuple[float, float, float, float], ...]) -> str:
    parts = [
        f"({h_lon} {h_lat}, {t_lon} {t_lat})"
        for h_lon, h_lat, t_lon, t_lat in quads
    ]
    return "SRID=4326;MULTILINESTRING(" + ", ".join(parts) + ")"


def resolve_rep_set(wh: Session, well: NarviInventoryWell) -> RepSet | None:
    """The representative stick set for one narvi well.

    Persisted ``novi_rep`` wins (narvi computed it at save, the engineer
    saw it). Legacy saves fall back to the SAME warehouse rule: pud/res
    pass-throughs resolve their own stick via ``unique_id`` (mode
    'self'); generated wells call the sql/35 function. PDP producers
    return None — no intel ML forecast exists for producers.
    """
    provenance = (well.category or "").lower()
    if provenance == "pdp":
        return None

    rep = well.novi_rep
    if rep and isinstance(rep.get("stick_ids"), list):
        ids = tuple(int(s) for s in rep["stick_ids"])
        return RepSet(
            mode=str(rep.get("mode") or "neighborhood"),
            stick_ids=ids,
            low_n=bool(rep.get("low_n", len(ids) < REP_LOW_N)),
            intel_vintage=rep.get("intel_vintage"),
            source="persisted",
        )

    if provenance in ("pud", "res"):
        uid = well.novi_wellname or well.well_name
        rows = wh.execute(_SELF_STICK_SQL, {"uid": uid}).all()
        if not rows:
            log.info("novi_rep_self_unresolved", well=well.well_name, uid=uid)
            return None
        return RepSet(
            mode="self",
            stick_ids=(int(rows[0][0]),),
            low_n=False,
            intel_vintage=None,
            source="fallback",
        )

    # generated
    if not well.legs_lonlat or not well.formation or not well.completed_lateral_ft:
        log.info("novi_rep_fallback_ungeoreferenced", well=well.well_name)
        return None
    rows = wh.execute(
        _REP_STICKS_SQL,
        {
            "legs": _legs_ewkt(well.legs_lonlat),
            "bench": _bench_code(well.formation),
            "lateral": well.completed_lateral_ft,
            "radius": REP_RADIUS_M,
            "tol": REP_LATERAL_TOL,
        },
    ).all()
    ids = tuple(int(r[0]) for r in rows)
    return RepSet(
        mode="neighborhood",
        stick_ids=ids,
        low_n=len(ids) < REP_LOW_N,
        intel_vintage=None,
        source="fallback",
    )


@dataclass(frozen=True)
class IntelMedianSeries:
    """Per-month median of the per-1,000-ft normalized stick forecasts,
    IP-aligned (index 0 = first forecast period), as 30-day-period
    VOLUMES (median rate x STEP_DAYS) — the novi_comparison sheet's
    vectors."""

    oil_bbl: tuple[float, ...]
    gas_mcf: tuple[float, ...]
    water_bbl: tuple[float, ...]
    n_sticks: int  # sticks actually in the median (ll_ft > 0 required)
    n_pud: int
    n_res: int
    dropped_sticks: tuple[int, ...]  # no ll_ft / no forecast rows — logged, not silent


def _tail_rates(segs: list[dict[str, Any]], days: list[float]) -> list[float]:
    """Arps rate at each tail day — a straight port of erebor's
    ``_tail_values`` (t in years since segment start, d_nom nominal/yr;
    exponential when the curve says so or b ~ 0; days covered by no
    segment stay 0)."""
    out = [0.0] * len(days)
    for seg in segs:
        day_start = float(seg["day_start"] or 0.0)
        day_stop = float(seg["day_stop"] or 0.0)
        qi = float(seg["q_start"] or 0.0)
        di = float(seg["d_nom"] or 0.0)
        b = float(seg["b"] or 0.0)
        exponential = seg["segment_curve_type"] == "exponential" or b < 1e-6
        for i, day in enumerate(days):
            if not (day_start <= day <= day_stop):
                continue
            t = (day - day_start) / 365.0
            if exponential:
                out[i] = qi * math.exp(-di * t)
            else:
                out[i] = qi * (1.0 + b * di * t) ** (-1.0 / b)
    return out


def fetch_intel_median_series(
    wh: Session, stick_ids: tuple[int, ...]
) -> IntelMedianSeries | None:
    """Median per-1,000-ft forecast across ``stick_ids``, or None when
    no stick yields a usable normalized series."""
    if not stick_ids:
        return None
    meta = wh.execute(_STICK_META_SQL, {"ids": list(stick_ids)}).all()
    by_name: dict[str, tuple[int, str, float]] = {}
    dropped: list[int] = []
    n_pud = n_res = 0
    for sid, uid, category, ll_ft, _basin in meta:
        if not ll_ft or float(ll_ft) <= 0.0:
            dropped.append(int(sid))
            log.info("novi_median_stick_no_lateral", stick_id=int(sid))
            continue
        by_name[str(uid)] = (int(sid), str(category), float(ll_ft))
        if str(category) == "PUD":
            n_pud += 1
        else:
            n_res += 1
    missing_meta = set(stick_ids) - {int(r[0]) for r in meta}
    for sid in sorted(missing_meta):
        # A persisted stick_id absent from the current vintage's
        # intel_locations (stick_id_map is append-only, membership is
        # not) — dropped and visible, never silent.
        dropped.append(sid)
        log.info("novi_median_stick_not_in_vintage", stick_id=sid)
    if not by_name:
        return None

    names = list(by_name)
    fc_rows = wh.execute(_FORECAST_SQL, {"names": names}).all()
    arps_rows = wh.execute(_ARPS_SQL, {"names": names}).mappings().all()

    # Per-stick per-stream rate grid on 30-day periods, IP-aligned.
    rates: dict[str, dict[str, list[float]]] = {
        name: {s: [0.0] * N_MONTHS for s in _STREAMS} for name in names
    }
    last_mop: dict[str, int] = dict.fromkeys(names, 0)
    got_forecast: set[str] = set()
    for name, mop, oil, gas, water in fc_rows:
        if name not in rates:  # dropped at the meta stage (no lateral)
            continue
        idx = int(mop) - 1  # mop 1 = first 30-day period
        if not (0 <= idx < N_MONTHS):
            continue
        got_forecast.add(name)
        grid = rates[name]
        grid["oil"][idx] = float(oil or 0.0)
        grid["gas"][idx] = float(gas or 0.0)
        grid["water"][idx] = float(water or 0.0)
        last_mop[name] = max(last_mop[name], int(mop))

    segs_by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for arow in arps_rows:
        if arow["production_stream"] in _STREAMS:
            segs_by[(arow["novi_wellname"], arow["production_stream"])].append(
                dict(arow)
            )
    for name in names:
        start = last_mop[name]  # tail begins after the last forecast period
        tail_mops = list(range(start + 1, N_MONTHS + 1))
        if not tail_mops:
            continue
        tail_days = [float(m * STEP_DAYS) for m in tail_mops]
        for stream in _STREAMS:
            segs = segs_by.get((name, stream))
            if not segs:
                continue
            tv = _tail_rates(segs, tail_days)
            row = rates[name][stream]
            for k, m in enumerate(tail_mops):
                row[m - 1] = tv[k]

    # Sticks with neither forecast nor Arps rows would median in as
    # all-zero and silently drag the benchmark down — drop them loudly.
    usable = [n for n in names if n in got_forecast or any(
        (n, s) in segs_by for s in _STREAMS
    )]
    for name in names:
        if name not in usable:
            dropped.append(by_name[name][0])
            log.info("novi_median_stick_no_series", stick_id=by_name[name][0])
    if not usable:
        return None
    n_pud = sum(1 for n in usable if by_name[n][1] == "PUD")
    n_res = sum(1 for n in usable if by_name[n][1] != "PUD")

    def _median_volumes(stream: str) -> tuple[float, ...]:
        out = []
        for i in range(N_MONTHS):
            vals = [
                rates[n][stream][i] / (by_name[n][2] / 1000.0) for n in usable
            ]
            out.append(median(vals) * STEP_DAYS)
        return tuple(out)

    return IntelMedianSeries(
        oil_bbl=_median_volumes("oil"),
        gas_mcf=_median_volumes("gas"),
        water_bbl=_median_volumes("water"),
        n_sticks=len(usable),
        n_pud=n_pud,
        n_res=n_res,
        dropped_sticks=tuple(sorted(set(dropped))),
    )
