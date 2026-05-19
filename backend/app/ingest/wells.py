"""Upsert wells from WellHeader DTOs.

Idempotent — same WellHeader applied twice produces no schema drift. The
raw upstream payload lands in `wells.raw_payload` JSONB so we can backfill
new typed columns later without re-pulling.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Well, WellStatus, WellstickSource
from app.enverus_client.base import WellHeader

log = get_logger("ingest.wells")


def _status_from_enverus(raw: str | None) -> WellStatus:
    """Map the freeform Enverus status string to our enum.

    The exact strings vary; we bias toward PDP/PA/SI/TA and fall back to
    UNKNOWN rather than crashing on unrecognized values.
    """
    if raw is None:
        return WellStatus.UNKNOWN
    s = raw.upper().strip()
    if any(x in s for x in ("PRODUC", "PDP", "ACTIVE")):
        return WellStatus.PDP
    if "PLUG" in s or s == "PA":
        return WellStatus.PA
    if "SHUT" in s or s == "SI":
        return WellStatus.SI
    if "ABANDON" in s and "TEMP" in s or s == "TA":
        return WellStatus.TA
    if "INACTIVE" in s:
        return WellStatus.INACTIVE
    return WellStatus.UNKNOWN


def _point_or_none(lon: float | None, lat: float | None) -> Any | None:
    if lon is None or lat is None:
        return None
    return func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)


def upsert_well_headers(session: Session, headers: Iterable[WellHeader]) -> int:
    """Upsert a batch. Returns the count of rows touched.

    Wellstick is left alone here — survey ingest is the canonical writer
    (heel point depends on survey data). For a well with no survey yet we
    seed wellstick_source = NONE and let `recompute_wellstick` upgrade it
    after the survey arrives.
    """
    n = 0
    now = datetime.now(timezone.utc)
    for h in headers:
        values: dict[str, Any] = {
            "api14": h.api14,
            "name": h.name,
            "operator": h.operator,
            "formation": h.formation,
            "first_prod_date": h.first_prod_date,
            "lateral_ft": h.lateral_ft,
            "proppant_lbs": h.proppant_lbs,
            "fluid_bbl": h.fluid_bbl,
            "stages": h.stages,
            "tvd_ft": h.tvd_ft,
            "county": h.county,
            "basin": h.basin,
            "status": _status_from_enverus(h.status).value,
            "sh_geom": _point_or_none(h.sh_lon, h.sh_lat),
            "bh_geom": _point_or_none(h.bh_lon, h.bh_lat),
            "last_synced_at": now,
            "raw_payload": h.raw or None,
        }

        stmt = pg_insert(Well.__table__).values(**values)
        # ON CONFLICT (api14) DO UPDATE SET ... — every column except api14
        # and the heel/wellstick fields (those are owned by survey ingest).
        update_cols = {
            c: stmt.excluded[c]
            for c in values
            if c not in {"api14"}
        }
        stmt = stmt.on_conflict_do_update(index_elements=["api14"], set_=update_cols)
        session.execute(stmt)
        n += 1
    session.commit()
    log.info("upsert_well_headers", count=n)
    return n


def well_exists(session: Session, api14: str) -> bool:
    return session.scalar(select(Well.api14).where(Well.api14 == api14)) is not None


def well_has_no_survey_marker(session: Session, api14: str) -> bool:
    """True if the well exists with wellstick_source = NONE — i.e. we never
    successfully ingested a survey for it. Used by the orchestrator to
    decide which wells still need a survey pull."""
    src: WellstickSource | None = session.scalar(
        select(Well.wellstick_source).where(Well.api14 == api14)
    )
    return src == WellstickSource.NONE
