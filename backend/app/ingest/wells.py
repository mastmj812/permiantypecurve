"""Upsert wells from ``warehouse_client.base.WellHeader`` DTOs.

Idempotent — the same ``WellHeader`` applied twice produces no schema
drift. Consumes api10-keyed DTOs from the warehouse_client data layer
(post-cutover, migration 0010); the legacy enverus_client path is
retired in this commit.

Status mapping happens upstream in ``warehouse_client/wells.py``
(``_status_from_curated``), so the DTO carries an already-remapped
string from the app's ``WellStatus`` vocabulary. ``_validated_status``
below just guards against the warehouse remap ever drifting from the
app enum.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Well, WellStatus
from app.warehouse_client.base import WellHeader

log = get_logger("ingest.wells")


_KNOWN_STATUSES: frozenset[str] = frozenset(m.value for m in WellStatus)


def _validated_status(raw: str) -> str:
    """Pass through if it's a known app status; otherwise log and fall
    back to UNKNOWN. The warehouse remap should always produce a known
    value — this is belt-and-suspenders.
    """
    if raw in _KNOWN_STATUSES:
        return raw
    log.warning("unknown_well_status", value=raw)
    return WellStatus.UNKNOWN.value


def _point_or_none(lon: float | None, lat: float | None) -> Any | None:
    if lon is None or lat is None:
        return None
    return func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)


def _wellstick_or_none(wkt: str | None) -> Any | None:
    if not wkt:
        return None
    return func.ST_GeomFromText(wkt, 4326)


def header_to_upsert_values(h: WellHeader, now: datetime) -> dict[str, Any]:
    """Turn a warehouse ``WellHeader`` into the values dict the upsert
    passes to PostGIS.

    The warehouse pre-computes the 4-point wellstick (Novi SHL→LP→MP→BHL,
    served as WKT via ST_AsText) and pre-remaps status, so this is a near-
    direct passthrough with no fallback chains.
    """
    return {
        "api10": h.api10,
        "api14": h.api14,
        "name": h.name,
        "operator": h.operator,
        "formation": h.formation,
        "first_prod_date": h.first_prod_date,
        "lateral_ft": h.lateral_ft,
        "proppant_lbs": h.proppant_lbs,
        "fluid_bbl": h.fluid_bbl,
        "tvd_ft": h.tvd_ft,
        "county": h.county,
        "basin": h.basin,
        "status": _validated_status(h.status),
        "sh_geom": _point_or_none(h.sh_lon, h.sh_lat),
        "bh_geom": _point_or_none(h.bh_lon, h.bh_lat),
        "wellstick": _wellstick_or_none(h.wellstick_wkt),
        "novi_oil_eur": h.novi_oil_eur,
        "last_synced_at": now,
    }


def upsert_well_headers(session: Session, headers: Iterable[WellHeader]) -> int:
    """Upsert a batch. Returns the count of rows touched.

    ON CONFLICT (api10) DO UPDATE — every column we passed except api10
    itself. Columns not in the values dict are left at their existing
    values; nothing in the new DTO shape is conditional today, but the
    pattern keeps the path open for future partial updates.
    """
    n = 0
    now = datetime.now(timezone.utc)
    for h in headers:
        values = header_to_upsert_values(h, now)
        stmt = pg_insert(Well.__table__).values(**values)
        update_cols = {c: stmt.excluded[c] for c in values if c != "api10"}
        stmt = stmt.on_conflict_do_update(
            index_elements=["api10"], set_=update_cols
        )
        session.execute(stmt)
        n += 1
    session.commit()
    log.info("upsert_well_headers", count=n)
    return n


def well_exists(session: Session, api10: str) -> bool:
    return (
        session.scalar(select(Well.api10).where(Well.api10 == api10)) is not None
    )
