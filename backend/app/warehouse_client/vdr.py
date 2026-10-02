"""Read seller data-room (VDR) production from the warehouse ``vdr`` schema.

engineering_db sql/53 lands seller ARIES packages verbatim, one
``vdr_id`` per data room (``scripts.load_vdr``). anduin copies the daily
rows for the deal's data room into ``vdr_daily_production`` and fits PDP
forecasts from them (``app.pdp.service``). Read-only, like every
warehouse_client module.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class VdrSource:
    vdr_id: str
    deal: str
    vendor_format: str
    loaded_at: Any
    data_through: date | None
    n_wells: int


_SOURCES_SQL = text(
    """
    SELECT s.vdr_id, s.deal, s.vendor_format, s.loaded_at,
           (SELECT max(data_through) FROM vdr.load_file f WHERE f.vdr_id = s.vdr_id) AS data_through,
           (SELECT count(*) FROM vdr.property p
             WHERE p.vdr_id = s.vdr_id AND p.api10 IS NOT NULL) AS n_wells
    FROM vdr.source s
    ORDER BY s.loaded_at DESC
    """
)

_WELLS_SQL = text(
    """
    SELECT api10, propnum, well_name, reserve_category, land_zone, first_prod_date
    FROM vdr.property
    WHERE vdr_id = :vdr_id AND api10 IS NOT NULL
    ORDER BY well_name
    """
)

_DAILY_SQL = text(
    """
    SELECT api10, well_name, reserve_category, prod_date,
           oil_bbl, gas_mcf, water_bbl, tbg_psi, csg_psi, choke, bhp_psi
    FROM vdr.well_daily
    WHERE vdr_id = :vdr_id AND api10 = ANY(:api10s)
    ORDER BY api10, prod_date
    """
)


def fetch_vdr_sources(wh: Session) -> list[VdrSource]:
    return [VdrSource(**dict(r)) for r in wh.execute(_SOURCES_SQL).mappings()]


def fetch_vdr_wells(wh: Session, vdr_id: str) -> list[dict[str, Any]]:
    """Seller properties that map to a suite well (api10 NOT NULL)."""
    return [dict(r) for r in wh.execute(_WELLS_SQL, {"vdr_id": vdr_id}).mappings()]


def fetch_vdr_daily(wh: Session, vdr_id: str, api10s: list[str]) -> Iterator[dict[str, Any]]:
    if not api10s:
        return
    yield from (
        dict(r) for r in wh.execute(_DAILY_SQL, {"vdr_id": vdr_id, "api10s": api10s}).mappings()
    )
