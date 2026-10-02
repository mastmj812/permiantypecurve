"""PDP forecasting service: sync a deal's data room, fit, transfer, persist.

Method of record lives in ``app.forecasting.daily``; this module is the
DB glue. Results go to ``pdp_forecasts`` (deal-scoped) — never to the
global ``forecasts`` table.

Unpeaked streams (managed-choke new wells) take Di from the same-bench
warehouse cohort: anduin's own per-well forecasts (``forecasts``, monthly
basis) for wells in the same donor bench group (``donor_benches`` —
``formation_blueox``, with WCXY and WCA_1 pooled) within 5 mi (10 mi
when fewer than ``MIN_DONORS``), >= 24 months of production, and a fitted
(not transferred) row for the stream. Median Di of the pool; b from the
bench prior (the same lender the monthly cohort transfer uses). No pool
-> the stream is left unforecast and flagged ``no_donors`` for manual.
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
from app.forecasting.types import ForecastConfig
from app.warehouse_client.vdr import fetch_vdr_daily, fetch_vdr_wells

log = get_logger("pdp.service")

DONOR_RADII_MI: tuple[float, ...] = (5.0, 10.0)
MIN_DONORS: int = 5
DONOR_MIN_MONTHS: int = 24
SELLER_PDP_CATEGORY: str = "1PDP"
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

    @classmethod
    def from_deal(cls, deal: Deal) -> PdpConfig:
        cfg = deal.pdp_config or {}
        if not cfg.get("vdr_id"):
            raise PdpConfigError(f"deal {deal.name!r} has no pdp_config.vdr_id")
        return cls(
            vdr_id=str(cfg["vdr_id"]),
            api10s=list(cfg["api10s"]) if cfg.get("api10s") else None,
            uptime_overrides={k: float(v) for k, v in (cfg.get("uptime_overrides") or {}).items()},
        )


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def resolve_api10s(wh: Session, cfg: PdpConfig) -> list[str]:
    """Explicit list, else every seller 1PDP property that maps to a well."""
    if cfg.api10s:
        return sorted(set(cfg.api10s))
    return sorted(
        {
            w["api10"]
            for w in fetch_vdr_wells(wh, cfg.vdr_id)
            if w["reserve_category"] == SELLER_PDP_CATEGORY
        }
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

_DONOR_SQL = text(
    """
    WITH tgt AS (
        SELECT COALESCE(wellstick, sh_geom) AS g
        FROM wells WHERE api10 = :api10
    )
    SELECT f.api10, f.di_initial, w.formation_blueox
    FROM tgt
    JOIN wells w ON w.formation_blueox = ANY(:benches) AND w.api10 <> :api10
    JOIN forecasts f ON f.api10 = w.api10 AND f.stream = CAST(:stream AS stream)
    WHERE f.di_initial IS NOT NULL
      AND f.fit_method NOT IN ('cohort_transfer', 'ratio_cum_oil')
      AND ST_DWithin(tgt.g::geography, COALESCE(w.wellstick, w.sh_geom)::geography, :radius_m)
      AND (SELECT count(*) FROM production_monthly p WHERE p.api10 = w.api10) >= :min_months
    """
)


def cohort_donors(
    session: Session, api10: str, stream: str, formation_blueox: str | None
) -> dict[str, Any] | None:
    benches = donor_benches(formation_blueox)
    if not benches:
        return None
    for radius in DONOR_RADII_MI:
        rows = session.execute(
            _DONOR_SQL,
            {
                "api10": api10,
                "stream": stream,
                "benches": benches,
                "radius_m": radius * _M_PER_MI,
                "min_months": DONOR_MIN_MONTHS,
            },
        ).all()
        if len(rows) >= MIN_DONORS:
            dis = np.array([r.di_initial for r in rows], dtype=float)
            return {
                "di_median": float(np.median(dis)),
                "di_p25": float(np.percentile(dis, 25)),
                "di_p75": float(np.percentile(dis, 75)),
                "n": len(rows),
                "radius_mi": radius,
                "min_months": DONOR_MIN_MONTHS,
                "benches": benches,
                "by_bench": {b: sum(r.formation_blueox == b for r in rows) for b in benches},
                "api10s": sorted(r.api10 for r in rows),
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
            if cls == "unpeaked" and window is None:
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
        out.append(
            StreamOutcome(
                stream, "transferred" if fc.method == "transfer_now" else "fitted", forecast=fc
            )
        )
    session.commit()
    return out


def forecast_deal(
    session: Session, deal: Deal, cfg: PdpConfig, api10s: list[str] | None = None
) -> dict[str, list[StreamOutcome]]:
    targets = api10s or synced_api10s(session, cfg.vdr_id)
    if not targets:
        raise PdpConfigError("no synced daily production — run the sync first")
    return {a: forecast_well(session, deal, cfg, a) for a in targets}


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
    """Engineer-set parameters (method ``manual``); volumes recomputed."""
    raw = load_daily(session, cfg.vdr_id, row.api10)
    prep = daily.prepare_daily(raw)
    fc = daily.DailyForecast(
        stream=row.stream.value,
        method="manual",
        params={"qi": qi, "Di": di, "b": b, "Df": row.df_terminal},
        anchor_date=anchor_date,
        data_through=prep["prod_date"].max().date(),
        fit_start=row.fit_start_date,
        diagnostics={
            "previous": {
                "method": row.method,
                "params": row.params,
                "anchor_date": row.anchor_date.isoformat(),
            }
        },
    )
    fc = replace(fc, tail_ratio=daily.tail_ratio(prep, fc.stream, fc.params, anchor_date))
    uptime = daily.compute_uptime(prep)
    breaks = daily.detect_breaks(prep)
    first_prod = date.fromisoformat(
        row.diagnostics.get("first_prod", prep["prod_date"].min().date().isoformat())
    )
    _persist(
        session,
        deal=deal,
        cfg=cfg,
        api10=row.api10,
        fc=fc,
        prep=prep,
        uptime=uptime,
        uptime_factor=row.uptime_factor,
        breaks=breaks,
        flags=daily.review_flags(fc, "fit", breaks, data_through=fc.data_through),
        first_prod=first_prod,
        horizon_years=float(row.diagnostics.get("horizon_years", 50.0)),
        manual=True,
    )
    session.commit()
