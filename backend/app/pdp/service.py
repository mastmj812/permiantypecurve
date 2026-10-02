"""PDP forecasting service: sync a deal's data room, fit, transfer, persist.

Method of record lives in ``app.forecasting.daily``; this module is the
DB glue. Results go to ``pdp_forecasts`` (deal-scoped) — never to the
global ``forecasts`` table.

Unpeaked streams (managed-choke new wells) take Di from a same-bench
offset cohort. Donors are SELECTED by criteria, not by whether someone
already forecast them (Michael, 2026-10-02): wells in the same donor
bench group (``donor_benches`` — ``formation_blueox``, WCXY and WCA_1
pooled) within 5 mi (10 mi when fewer than ``MIN_DONORS``), first prod
>= ``DONOR_MIN_FIRST_PROD``, >= 24 months of production. Candidates with
NO ``forecasts`` rows at all are autoforecast with the house monthly
engine and PERSISTED to the global ``forecasts`` table (reusable; they
show up as ordinary unreviewed machine fits). Candidates that already
have rows keep them untouched — refitting would overwrite unlocked
engineer edits — and lend whatever streams they have. Median Di of the
stream's fitted (not transferred / ratio) rows; b from the bench prior
(the same lender the monthly cohort transfer uses). No pool -> the
stream is left unforecast and flagged ``no_donors`` for manual.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Deal, PdpForecast, Stream, VdrDailyProduction, Well
from app.forecasting import daily
from app.forecasting.b_prior import lookup_b_prior
from app.forecasting.orchestrator import df_terminal_for_subbasin
from app.forecasting.orchestrator import forecast_well as autoforecast_well
from app.forecasting.types import ForecastConfig
from app.warehouse_client.vdr import fetch_vdr_daily, fetch_vdr_wells

log = get_logger("pdp.service")

DONOR_RADII_MI: tuple[float, ...] = (5.0, 10.0)
MIN_DONORS: int = 5
DONOR_MIN_MONTHS: int = 24
DONOR_MIN_FIRST_PROD: date = date(2016, 1, 1)
SELLER_PDP_CATEGORY: str = "1PDP"
SELLER_PDNP_CATEGORY: str = "2PDNP"
_M_PER_MI: float = 1609.344

# Benches pooled for the PDP donor cohort ONLY (Michael, 2026-10-02):
# WCXY sits a few tens of feet above WCA_1 (Reeves medians 10,522 vs
# 10,586 ft around Yorktown 68H) — the split is operator landing
# strategy, not a different target. Formation grouping elsewhere in the
# suite is unchanged.
DONOR_BENCH_GROUPS: tuple[frozenset[str], ...] = (frozenset({"WCXY", "WCA_1"}),)


def donor_benches(formation_blueox: str | None) -> list[str]:
    """Benches whose wells may lend Di to a well on ``formation_blueox``."""
    if formation_blueox is None:
        return []
    for group in DONOR_BENCH_GROUPS:
        if formation_blueox in group:
            return sorted(group)
    return [formation_blueox]


class PdpConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PdpConfig:
    vdr_id: str
    api10s: list[str] | None
    uptime_overrides: dict[str, float]
    include_pdnp: bool = False

    @classmethod
    def from_deal(cls, deal: Deal) -> PdpConfig:
        cfg = deal.pdp_config or {}
        if not cfg.get("vdr_id"):
            raise PdpConfigError(f"deal {deal.name!r} has no pdp_config.vdr_id")
        return cls(
            vdr_id=str(cfg["vdr_id"]),
            api10s=list(cfg["api10s"]) if cfg.get("api10s") else None,
            uptime_overrides={k: float(v) for k, v in (cfg.get("uptime_overrides") or {}).items()},
            include_pdnp=bool(cfg.get("include_pdnp", False)),
        )


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def resolve_api10s(wh: Session, cfg: PdpConfig) -> list[str]:
    """Explicit list, else every seller 1PDP property that maps to a well
    (plus 2PDNP when the deal conveys its non-producing wells)."""
    if cfg.api10s:
        return sorted(set(cfg.api10s))
    cats = {SELLER_PDP_CATEGORY} | ({SELLER_PDNP_CATEGORY} if cfg.include_pdnp else set())
    return sorted(
        {w["api10"] for w in fetch_vdr_wells(wh, cfg.vdr_id) if w["reserve_category"] in cats}
    )


def sync_vdr_daily(session: Session, wh: Session, cfg: PdpConfig) -> dict[str, Any]:
    """Replace the local copy of the data room's daily rows for the PDP set."""
    api10s = resolve_api10s(wh, cfg)
    if not api10s:
        raise PdpConfigError(f"vdr {cfg.vdr_id!r} has no seller {SELLER_PDP_CATEGORY} wells")
    rows = [{**r, "vdr_id": cfg.vdr_id} for r in fetch_vdr_daily(wh, cfg.vdr_id, api10s)]
    session.execute(delete(VdrDailyProduction).where(VdrDailyProduction.vdr_id == cfg.vdr_id))
    for i in range(0, len(rows), 1000):
        session.execute(insert(VdrDailyProduction), rows[i : i + 1000])
    session.commit()
    got = {r["api10"] for r in rows}
    return {
        "vdr_id": cfg.vdr_id,
        "wells": len(got),
        "rows": len(rows),
        "missing_api10s": sorted(set(api10s) - got),
        "data_through": max((r["prod_date"] for r in rows), default=None),
    }


def load_daily(session: Session, vdr_id: str, api10: str) -> pd.DataFrame:
    rows = session.execute(
        select(
            VdrDailyProduction.prod_date,
            VdrDailyProduction.oil_bbl,
            VdrDailyProduction.gas_mcf,
            VdrDailyProduction.water_bbl,
            VdrDailyProduction.choke,
            VdrDailyProduction.tbg_psi,
            VdrDailyProduction.csg_psi,
        )
        .where(VdrDailyProduction.vdr_id == vdr_id, VdrDailyProduction.api10 == api10)
        .order_by(VdrDailyProduction.prod_date)
    ).all()
    return pd.DataFrame(
        rows,
        columns=["prod_date", "oil_bbl", "gas_mcf", "water_bbl", "choke", "tbg_psi", "csg_psi"],
    )


def synced_api10s(session: Session, vdr_id: str) -> list[str]:
    return list(
        session.execute(
            select(VdrDailyProduction.api10)
            .where(VdrDailyProduction.vdr_id == vdr_id)
            .distinct()
            .order_by(VdrDailyProduction.api10)
        ).scalars()
    )


# ---------------------------------------------------------------------------
# Cohort donors
# ---------------------------------------------------------------------------

# Spatial filter FIRST (GiST bounding-box prefilter on wellstick / sh_geom,
# then the exact geography distance), production-month count only for the
# survivors. Counting first scanned the hypertable for every basin-wide
# bench well (~14k) — 11.6 s per call, ~3 min per alchemist run.
# :box_deg pads the box generously (1 deg lon ~ 94 km at 32 N).
_CANDIDATES_SQL = text(
    """
    WITH tgt AS (
        SELECT COALESCE(wellstick, sh_geom) AS g
        FROM wells WHERE api10 = :api10
    ), near AS MATERIALIZED (
        SELECT w.api10, w.formation_blueox
        FROM tgt
        JOIN wells w ON (
            w.wellstick && ST_Expand(tgt.g, :box_deg)
            OR w.sh_geom && ST_Expand(tgt.g, :box_deg)
        )
        WHERE w.formation_blueox = ANY(:benches)
          AND w.api10 <> :api10
          AND w.first_prod_date >= :min_first_prod
          AND ST_DWithin(tgt.g::geography, COALESCE(w.wellstick, w.sh_geom)::geography, :radius_m)
    )
    SELECT n.api10, n.formation_blueox,
           EXISTS (SELECT 1 FROM forecasts f WHERE f.api10 = n.api10) AS has_forecast
    FROM near n
    WHERE (SELECT count(*) FROM production_monthly p WHERE p.api10 = n.api10) >= :min_months
    """
)

_DONOR_DI_SQL = text(
    """
    SELECT f.api10, f.di_initial
    FROM forecasts f
    WHERE f.api10 = ANY(:api10s)
      AND f.stream = CAST(:stream AS stream)
      AND f.di_initial IS NOT NULL
      AND f.fit_method NOT IN ('cohort_transfer', 'ratio_cum_oil')
    """
)


def _ensure_donor_forecasts(session: Session, api10s: list[str]) -> list[str]:
    """Autoforecast + persist candidates that have NO forecast rows.
    Returns the api10s fitted now."""
    fitted: list[str] = []
    for a in api10s:
        try:
            res = autoforecast_well(session, a, persist=True)
        except Exception as e:  # a bad donor never sinks the target well
            log.warning("pdp_donor_autofit_failed", api10=a, err=str(e))
            continue
        if any(r is not None for r in res.values()):
            fitted.append(a)
    return fitted


def cohort_donors(
    session: Session, api10: str, stream: str, formation_blueox: str | None
) -> dict[str, Any] | None:
    benches = donor_benches(formation_blueox)
    if not benches:
        return None
    for radius in DONOR_RADII_MI:
        cands = session.execute(
            _CANDIDATES_SQL,
            {
                "api10": api10,
                "benches": benches,
                "min_first_prod": DONOR_MIN_FIRST_PROD,
                "radius_m": radius * _M_PER_MI,
                "box_deg": radius * _M_PER_MI / 60_000.0,
                "min_months": DONOR_MIN_MONTHS,
            },
        ).all()
        if len(cands) < MIN_DONORS:
            continue
        fitted_now = set(
            _ensure_donor_forecasts(session, [c.api10 for c in cands if not c.has_forecast])
        )
        bench_of = {c.api10: c.formation_blueox for c in cands}
        rows = session.execute(_DONOR_DI_SQL, {"api10s": list(bench_of), "stream": stream}).all()
        if len(rows) < MIN_DONORS:
            continue
        dis = np.array([r.di_initial for r in rows], dtype=float)
        used = sorted(r.api10 for r in rows)
        return {
            "di_median": float(np.median(dis)),
            "di_p25": float(np.percentile(dis, 25)),
            "di_p75": float(np.percentile(dis, 75)),
            "n": len(rows),
            "n_candidates": len(cands),
            "n_autofit_now": len(fitted_now & set(used)),
            "radius_mi": radius,
            "min_months": DONOR_MIN_MONTHS,
            "min_first_prod": DONOR_MIN_FIRST_PROD.isoformat(),
            "benches": benches,
            "by_bench": {b: sum(bench_of[a] == b for a in used) for b in benches},
            "api10s": used,
            "autofit_api10s": sorted(fitted_now & set(used)),
        }
    return None


# ---------------------------------------------------------------------------
# Fit one well
# ---------------------------------------------------------------------------


@dataclass
class StreamOutcome:
    stream: str
    status: str  # fitted | transferred | skipped_locked | no_production | no_donors | failed
    detail: str | None = None
    forecast: daily.DailyForecast | None = None


def _well_meta(session: Session, api10: str) -> tuple[str | None, str | None]:
    row = session.execute(
        select(Well.subbasin, Well.formation_blueox).where(Well.api10 == api10)
    ).one_or_none()
    return (row.subbasin, row.formation_blueox) if row is not None else (None, None)


def _existing(session: Session, deal_id: uuid.UUID, api10: str, stream: str) -> PdpForecast | None:
    return session.execute(
        select(PdpForecast).where(
            PdpForecast.deal_id == deal_id,
            PdpForecast.api10 == api10,
            PdpForecast.stream == Stream(stream),
        )
    ).scalar_one_or_none()


def _persist(
    session: Session,
    *,
    deal: Deal,
    cfg: PdpConfig,
    api10: str,
    fc: daily.DailyForecast,
    prep: pd.DataFrame,
    uptime: daily.Uptime,
    uptime_factor: float,
    breaks: list[dict[str, Any]],
    flags: list[str],
    first_prod: date,
    horizon_years: float,
    manual: bool,
) -> None:
    cum = daily.cum_to_date(prep, fc.stream)
    remaining = daily.remaining_volume(fc, uptime_factor, first_prod, horizon_years)
    basis = uptime.to_dict()
    if uptime_factor != uptime.factor:
        basis = {**basis, "override": uptime_factor}
    diagnostics = {
        **fc.diagnostics,
        "peak_rate": fc.peak_rate,
        "at_bound": fc.at_bound,
        "anchor_effective_decline": round(daily.anchor_effective_decline(fc), 4),
        "forward_effective_decline": round(daily.forward_effective_decline(fc), 4),
        "first_prod": first_prod.isoformat(),
        "horizon_years": horizon_years,
    }
    now = datetime.now(UTC)
    values = {
        "id": uuid.uuid4(),
        "deal_id": deal.id,
        "vdr_id": cfg.vdr_id,
        "api10": api10,
        "stream": fc.stream,
        "method": fc.method,
        "model_type": daily.MODEL_TYPE,
        "params": fc.params,
        "qi": fc.qi,
        "di_initial": fc.di,
        "b": fc.b,
        "df_terminal": fc.params["Df"],
        "anchor_date": fc.anchor_date,
        "fit_start_date": fc.fit_start,
        "data_through": fc.data_through,
        "uptime_factor": uptime_factor,
        "uptime_basis": basis,
        "fit_r2_log": fc.fit_r2_log,
        "fit_rmse_log": fc.fit_rmse_log,
        "n_points": fc.n_points,
        "tail_ratio": fc.tail_ratio,
        "cum_to_date": cum,
        "remaining": remaining,
        "eur": cum + remaining,
        "review_flags": flags,
        "breaks": breaks,
        "diagnostics": diagnostics,
        "manual_override": manual,
        "locked": False,
        "created_at": now,
        "updated_at": now,
    }
    stmt = pg_insert(PdpForecast).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_pdp_forecasts_deal_api10_stream",
        set_={
            c: stmt.excluded[c]
            for c in values
            if c not in {"id", "deal_id", "api10", "stream", "created_at", "locked"}
        },
    )
    session.execute(stmt)


def forecast_well(
    session: Session,
    deal: Deal,
    cfg: PdpConfig,
    api10: str,
    *,
    streams: tuple[str, ...] = daily.STREAMS,
    fit_start: dict[str, date | None] | None = None,
    config: ForecastConfig | None = None,
) -> list[StreamOutcome]:
    """Fit (or transfer) each stream and upsert. Locked rows are skipped.

    ``fit_start`` maps stream -> engineer window start; a stream absent
    from the map keeps its persisted window (if any) so a refit never
    silently drops the engineer's choice. An explicit None clears it.
    """
    base_cfg = config or ForecastConfig()
    subbasin, formation_blueox = _well_meta(session, api10)
    df_terminal = df_terminal_for_subbasin(subbasin, base_cfg)
    raw = load_daily(session, cfg.vdr_id, api10)
    if raw.empty:
        return [StreamOutcome(s, "no_production", "no synced daily rows") for s in streams]
    prep = daily.prepare_daily(raw, base_cfg)
    uptime = daily.compute_uptime(prep)
    uptime_factor = cfg.uptime_overrides.get(api10, uptime.factor)
    breaks = daily.detect_breaks(prep)
    through = prep["prod_date"].max().date()
    producing = prep.loc[~prep["well_down"], "prod_date"]
    first_prod = (producing.min() if len(producing) else prep["prod_date"].min()).date()

    out: list[StreamOutcome] = []
    for stream in streams:
        existing = _existing(session, deal.id, api10, stream)
        if existing is not None and existing.locked:
            out.append(StreamOutcome(stream, "skipped_locked"))
            continue
        if (
            existing is not None
            and existing.method == "manual"
            and (fit_start is None or stream not in fit_start)
        ):
            # Engineer-set parameters survive every re-run (sync, deal-wide
            # forecast, uptime change): keep the params, recompute volumes
            # on the current data and uptime. Only an explicit window refit
            # (or new manual params) replaces them.
            _persist_manual(
                session,
                deal=deal,
                cfg=cfg,
                api10=api10,
                stream=stream,
                params=dict(existing.params),
                anchor_date=existing.anchor_date,
                fit_start=existing.fit_start_date,
                prep=prep,
                uptime=uptime,
                uptime_factor=uptime_factor,
                breaks=breaks,
                first_prod=first_prod,
                horizon_years=base_cfg.horizon_years,
                diagnostics={k: v for k, v in existing.diagnostics.items() if k == "previous"},
            )
            out.append(StreamOutcome(stream, "kept_manual"))
            continue
        window = (
            fit_start[stream]
            if fit_start is not None and stream in fit_start
            else (existing.fit_start_date if existing is not None else None)
        )
        peak = daily.detect_daily_peak(prep, stream)
        cls = daily.classify_stream(prep, stream, peak)
        prior = lookup_b_prior(subbasin, formation_blueox, stream)
        try:
            if cls == "no_production":
                out.append(StreamOutcome(stream, "no_production"))
                continue
            if cls == "shut_in" and window is None:
                fc = daily.shut_in_forecast(prep, stream, df_terminal=df_terminal)
            elif cls == "unpeaked" and window is None:
                donors = cohort_donors(session, api10, stream, formation_blueox)
                if donors is None:
                    out.append(
                        StreamOutcome(
                            stream,
                            "no_donors",
                            f"< {MIN_DONORS} same-bench donors within {DONOR_RADII_MI[-1]:.0f} mi",
                        )
                    )
                    continue
                donors = {**donors, "b_source": prior.source, "b_key": prior.key}
                fc = daily.transfer_now(
                    prep,
                    stream,
                    cohort_di=donors["di_median"],
                    b=prior.b,
                    df_terminal=df_terminal,
                    donors=donors,
                )
            else:
                assert peak is not None
                fc = daily.fit_daily_stream(
                    prep,
                    stream,
                    peak,
                    df_terminal=df_terminal,
                    config=base_cfg,
                    b_prior=prior.b if base_cfg.b_prior_enabled else None,
                    fit_start=window,
                )
        except Exception as e:  # one bad stream never sinks the well
            log.exception("pdp_fit_failed", api10=api10, stream=stream, err=str(e))
            out.append(StreamOutcome(stream, "failed", str(e)))
            continue
        flags = daily.review_flags(fc, cls, breaks, data_through=through)
        if fc.method == "transfer_now" and stream == "water" and api10.startswith("42"):
            # Donor water fits ride Novi TX water, which is a fixed
            # 0.970 x gas allocation — the transferred water Di is gas-shaped.
            flags.append("donor_water_allocated")
        _persist(
            session,
            deal=deal,
            cfg=cfg,
            api10=api10,
            fc=fc,
            prep=prep,
            uptime=uptime,
            uptime_factor=uptime_factor,
            breaks=breaks,
            flags=flags,
            first_prod=first_prod,
            horizon_years=base_cfg.horizon_years,
            manual=window is not None,
        )
        status = {"transfer_now": "transferred", "shut_in": "shut_in"}.get(fc.method, "fitted")
        out.append(StreamOutcome(stream, status, forecast=fc))
    session.commit()
    return out


def forecast_deal(
    session: Session, deal: Deal, cfg: PdpConfig, api10s: list[str] | None = None
) -> dict[str, list[StreamOutcome]]:
    targets = api10s or synced_api10s(session, cfg.vdr_id)
    if not targets:
        raise PdpConfigError("no synced daily production — run the sync first")
    return {a: forecast_well(session, deal, cfg, a) for a in targets}


def _first_prod(prep: pd.DataFrame) -> date:
    producing = prep.loc[~prep["well_down"], "prod_date"]
    return (producing.min() if len(producing) else prep["prod_date"].min()).date()


def _persist_manual(
    session: Session,
    *,
    deal: Deal,
    cfg: PdpConfig,
    api10: str,
    stream: str,
    params: dict[str, float],
    anchor_date: date,
    fit_start: date | None,
    prep: pd.DataFrame,
    uptime: daily.Uptime,
    uptime_factor: float,
    breaks: list[dict[str, Any]],
    first_prod: date,
    horizon_years: float,
    diagnostics: dict[str, Any],
) -> None:
    fc = daily.DailyForecast(
        stream=stream,
        method="manual",
        params=params,
        anchor_date=anchor_date,
        data_through=prep["prod_date"].max().date(),
        fit_start=fit_start,
        diagnostics=diagnostics,
    )
    fc = replace(fc, tail_ratio=daily.tail_ratio(prep, stream, params, anchor_date))
    _persist(
        session,
        deal=deal,
        cfg=cfg,
        api10=api10,
        fc=fc,
        prep=prep,
        uptime=uptime,
        uptime_factor=uptime_factor,
        breaks=breaks,
        flags=daily.review_flags(fc, "fit", breaks, data_through=fc.data_through),
        first_prod=first_prod,
        horizon_years=horizon_years,
        manual=True,
    )


def set_manual_params(
    session: Session,
    deal: Deal,
    cfg: PdpConfig,
    row: PdpForecast,
    *,
    qi: float,
    di: float,
    b: float,
    anchor_date: date,
) -> None:
    """Engineer-set parameters (method ``manual``); volumes recomputed.
    The replaced fit is kept under ``diagnostics.previous`` (one level —
    a manual-over-manual edit keeps the original machine fit there)."""
    prep = daily.prepare_daily(load_daily(session, cfg.vdr_id, row.api10))
    uptime = daily.compute_uptime(prep)
    previous = row.diagnostics.get("previous") if row.method == "manual" else None
    _persist_manual(
        session,
        deal=deal,
        cfg=cfg,
        api10=row.api10,
        stream=row.stream.value,
        params={"qi": qi, "Di": di, "b": b, "Df": row.df_terminal},
        anchor_date=anchor_date,
        fit_start=row.fit_start_date,
        prep=prep,
        uptime=uptime,
        uptime_factor=cfg.uptime_overrides.get(row.api10, uptime.factor),
        breaks=daily.detect_breaks(prep),
        first_prod=_first_prod(prep),
        horizon_years=float(row.diagnostics.get("horizon_years", 50.0)),
        diagnostics={
            "previous": previous
            or {
                "method": row.method,
                "params": row.params,
                "anchor_date": row.anchor_date.isoformat(),
                "fit_start_date": row.fit_start_date.isoformat() if row.fit_start_date else None,
            }
        },
    )
    session.commit()


SERIES_STEP_DAYS: int = 7


def stream_series(
    session: Session, cfg: PdpConfig, row: PdpForecast, years_ahead: float
) -> dict[str, Any]:
    """Chart payload for one stream: every calendar day's actual
    producing-day rate with its down flag, and the model's producing-day
    rate from the anchor to data_through + ``years_ahead`` (weekly)."""
    prep = daily.prepare_daily(load_daily(session, cfg.vdr_id, row.api10))
    stream = row.stream.value
    col = daily.VOLUME_COL[stream]
    down = prep[f"{stream}_down"] | prep["well_down"]
    actual = [
        {"d": d.date().isoformat(), "q": None if pd.isna(q) else float(q), "down": bool(dn)}
        for d, q, dn in zip(prep["prod_date"], prep[col], down, strict=True)
    ]
    end = row.data_through + pd.Timedelta(days=round(years_ahead * daily.DAYS_PER_YEAR_CAL))
    days = pd.date_range(row.anchor_date, end, freq=f"{SERIES_STEP_DAYS}D")
    if len(days) == 0 or days[-1].date() != row.data_through:
        days = days.union(pd.DatetimeIndex([pd.Timestamp(row.data_through)]))
    q = daily.model_rate(dict(row.params), row.anchor_date, days)
    events = [
        {
            "start": prep["prod_date"].iloc[a].date().isoformat(),
            "end": prep["prod_date"].iloc[b - 1].date().isoformat(),
        }
        for a, b in daily.true_runs(prep["event_down"].to_numpy())
    ]
    return {
        "api10": row.api10,
        "stream": stream,
        "method": row.method,
        "anchor_date": row.anchor_date.isoformat(),
        "fit_start_date": row.fit_start_date.isoformat() if row.fit_start_date else None,
        "data_through": row.data_through.isoformat(),
        "uptime_factor": row.uptime_factor,
        "actual": actual,
        "model": [{"d": d.date().isoformat(), "q": float(v)} for d, v in zip(days, q, strict=True)],
        "events": events,
        "breaks": row.breaks,
    }
