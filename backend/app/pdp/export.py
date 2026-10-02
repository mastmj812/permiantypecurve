"""Assemble the Blue Ox PDP workbook (contract §2) from pdp_forecasts.

Glue only: reads the deal's persisted PDP rows + synced daily volumes,
builds each well's monthly vectors on the workbook grid, groups them,
and hands :class:`app.exports.blueox_pdp.PdpExportData` to the pure
builder. Volume rule per calendar day (``delivered_daily``):

    day <= data_through          seller-reported daily volume (as reported)
    data_through < day <= horizon  model producing-day rate x uptime
    day > horizon                 0   (horizon = first prod + 50 yr)

Export inputs live in ``deals.pdp_config["export"]``:
``{effective_date, grouping: "well" | "lease" | "custom",
groups: {name: [api10, ...]} (custom only), curve_months}``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Deal, PdpForecast, VdrDailyProduction
from app.exports.blueox_pdp import (
    PDP_STREAMS,
    PdpExportData,
    PdpGroup,
    PdpWell,
    first_row_month,
)
from app.forecasting import daily
from app.pdp.service import PdpConfig, load_daily

DEFAULT_CURVE_MONTHS = 600
GROUPINGS: tuple[str, ...] = ("well", "lease", "custom")


class PdpExportError(ValueError):
    pass


@dataclass(frozen=True)
class ExportConfig:
    effective_date: date
    grouping: str
    groups: dict[str, list[str]]
    curve_months: int
    supersedes: str | None = None

    @classmethod
    def from_deal(cls, deal: Deal) -> ExportConfig:
        raw = (deal.pdp_config or {}).get("export") or {}
        if not raw.get("effective_date"):
            raise PdpExportError(
                "set the deal effective date (Blue Ox kickoff input) before exporting"
            )
        grouping = raw.get("grouping") or "well"
        if grouping not in GROUPINGS:
            raise PdpExportError(f"grouping must be one of {GROUPINGS}")
        curve_months = int(
            raw.get("curve_months")
            or (deal.blueox_config or {}).get("curve_months")
            or DEFAULT_CURVE_MONTHS
        )
        return cls(
            effective_date=date.fromisoformat(str(raw["effective_date"])),
            grouping=grouping,
            groups={str(k): [str(a) for a in v] for k, v in (raw.get("groups") or {}).items()},
            curve_months=curve_months,
            supersedes=raw.get("supersedes") or None,
        )


def month_starts(first: date, n: int) -> list[date]:
    out, y, m = [], first.year, first.month
    for _ in range(n):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def delivered_daily(
    actual: pd.Series,
    *,
    params: dict[str, float] | None,
    anchor: date | None,
    data_through: date,
    uptime: float,
    start: date,
    end: date,
    horizon_end: date,
) -> pd.Series:
    """Calendar-day volumes on [start, end]. ``actual`` is the seller's
    daily volume indexed by date (NaN = no report -> 0). ``params`` None
    = no forecast for the stream (zeros after data_through)."""
    days = pd.date_range(start, end, freq="D")
    out = pd.Series(0.0, index=days)
    hist = days[days <= pd.Timestamp(data_through)]
    if len(hist):
        out.loc[hist] = actual.reindex(hist).fillna(0.0).to_numpy(dtype=float)
    fut = days[(days > pd.Timestamp(data_through)) & (days <= pd.Timestamp(horizon_end))]
    if len(fut) and params is not None and anchor is not None:
        out.loc[fut] = daily.model_rate(params, anchor, fut) * uptime
    return out


def monthly_vector(daily_vols: pd.Series, months: list[date]) -> list[float]:
    by_month = daily_vols.groupby(daily_vols.index.to_period("M")).sum()
    return [float(by_month.get(pd.Period(m, "M"), 0.0)) for m in months]


def lease_of(well_name: str) -> str:
    """'Sheridan 27A Unit 73H' -> 'Sheridan 27A Unit' (drop the well number)."""
    parts = well_name.rsplit(" ", 1)
    return parts[0] if len(parts) == 2 else well_name


def build_well(
    api10: str,
    well_name: str,
    rows: dict[str, PdpForecast],
    raw_daily: pd.DataFrame,
    months: list[date],
) -> PdpWell:
    prep = daily.prepare_daily(raw_daily)
    data_through = prep["prod_date"].max().date()
    start = months[0]
    nxt = months[-1]
    end = (date(nxt.year + (nxt.month == 12), nxt.month % 12 + 1, 1)) - timedelta(days=1)
    any_row = next(iter(rows.values()))
    first_prod = date.fromisoformat(
        any_row.diagnostics.get("first_prod", prep["prod_date"].min().date().isoformat())
    )
    horizon = daily.horizon_end(first_prod, float(any_row.diagnostics.get("horizon_years", 50.0)))
    actual_idx = prep.set_index("prod_date")
    volumes: dict[str, list[float]] = {}
    for s in PDP_STREAMS:
        r = rows.get(s)
        vols = delivered_daily(
            actual_idx[daily.VOLUME_COL[s]],
            params=dict(r.params) if r is not None else None,
            anchor=r.anchor_date if r is not None else None,
            data_through=data_through,
            uptime=r.uptime_factor if r is not None else any_row.uptime_factor,
            start=start,
            end=end,
            horizon_end=horizon,
        )
        volumes[s] = monthly_vector(vols, months)
    return PdpWell(
        api10=api10,
        well_name=well_name,
        volumes=volumes,
        methods={s: (rows[s].method if s in rows else "none") for s in PDP_STREAMS},
        locked={s: bool(rows[s].locked) if s in rows else False for s in PDP_STREAMS},
        review_flags=sorted({f for r in rows.values() for f in r.review_flags}),
        uptime_factor=any_row.uptime_factor,
    )


def assemble(session: Session, deal: Deal, cfg: PdpConfig, exp: ExportConfig) -> PdpExportData:
    rows = list(
        session.execute(select(PdpForecast).where(PdpForecast.deal_id == deal.id)).scalars()
    )
    if not rows:
        raise PdpExportError("no PDP forecasts for this deal — sync and run the forecast first")
    by_well: dict[str, dict[str, PdpForecast]] = defaultdict(dict)
    for r in rows:
        by_well[r.api10][r.stream.value] = r
    names = dict(
        session.execute(
            select(VdrDailyProduction.api10, VdrDailyProduction.well_name)
            .where(VdrDailyProduction.vdr_id == cfg.vdr_id)
            .distinct()
        ).all()
    )
    first = first_row_month(exp.effective_date)
    months = month_starts(first, exp.curve_months)
    wells = {
        a: build_well(a, names.get(a) or a, streams, load_daily(session, cfg.vdr_id, a), months)
        for a, streams in sorted(by_well.items())
    }

    if exp.grouping == "well":
        groups = [
            PdpGroup(w.well_name, [w]) for w in sorted(wells.values(), key=lambda w: w.well_name)
        ]
    elif exp.grouping == "lease":
        leases: dict[str, list[PdpWell]] = defaultdict(list)
        for w in wells.values():
            leases[lease_of(w.well_name)].append(w)
        groups = [
            PdpGroup(k, sorted(v, key=lambda w: w.well_name)) for k, v in sorted(leases.items())
        ]
    else:
        listed = {a for v in exp.groups.values() for a in v}
        unknown = sorted(listed - set(wells))
        missing = sorted(set(wells) - listed)
        if unknown or missing:
            raise PdpExportError(
                f"custom grouping must cover every PDP well exactly: unknown {unknown}, unassigned {missing}"
            )
        groups = [PdpGroup(k, [wells[a] for a in v]) for k, v in exp.groups.items()]

    data_through = max(r.data_through for r in rows)
    fit_stamp = session.execute(
        select(func.max(PdpForecast.updated_at)).where(PdpForecast.deal_id == deal.id)
    ).scalar_one()
    bx = deal.blueox_config or {}
    return PdpExportData(
        codename=str(bx.get("codename") or deal.name),
        export_date=datetime.now(UTC).date(),
        effective_date=exp.effective_date,
        first_row_month=first,
        curve_months=exp.curve_months,
        groups=groups,
        production_history_through=data_through,
        source_system=f"anduin pdp_forecasts on seller VDR daily production ({cfg.vdr_id})",
        curve_params_source=f"pdp_forecasts as of {fit_stamp:%Y-%m-%d %H:%M} UTC"
        if fit_stamp
        else "pdp_forecasts",
        prepared_by=str(bx.get("prepared_by") or ""),
        vdr_id=cfg.vdr_id,
        zone_names=[str(z.get("name")) for z in (bx.get("zones") or []) if z.get("name")],
        supersedes=exp.supersedes,
        extra_manifest=[
            ("grouping", exp.grouping),
            ("review_status", f"{sum(r.locked for r in rows)} of {len(rows)} well-streams locked"),
        ],
    )


def preview(data: PdpExportData) -> dict[str, Any]:
    from app.exports.blueox_pdp import group_totals, pdp_filename, readiness

    return {
        "filename": pdp_filename(data.codename, data.export_date),
        "first_row_month": data.first_row_month.isoformat(),
        "curve_months": data.curve_months,
        "production_history_through": data.production_history_through.isoformat(),
        "groups": [
            {
                "name": g.name,
                "well_count": len(g.wells),
                **{
                    f"eur_{s}": float(np.sum(group_totals(g, data.curve_months)[s]))
                    for s in PDP_STREAMS
                },
            }
            for g in data.groups
        ],
        "findings": [f.__dict__ for f in readiness(data)],
    }
