"""Upsert wells from WellHeader DTOs.

Idempotent — same WellHeader applied twice produces no schema drift. The
raw upstream payload lands in `wells.raw_payload` JSONB so we can backfill
new typed columns later without re-pulling.

When a WellHeader carries a `lateral_line_wkt`, we use it as the canonical
wellstick at header-insert time, bypassing the survey-fetch / heel-from-
inclination pipeline for that well. Coverage on horizontal Permian wells
is ~91% via this path (vs ~53% via our survey fetch). The survey path
remains the fallback for wells without LateralLine — see `sync_county` /
`ingest/surveys.py`.
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


def header_to_upsert_values(h: WellHeader, now: datetime) -> dict[str, Any]:
    """Turn a WellHeader into the values dict the upsert passes to PostGIS.

    Pulled out as a pure function so unit tests can assert on the shape
    (which columns get included for which header shape) without standing
    up a real session. The DB-side ST_GeomFromText / ST_MakePoint calls
    only resolve at execute time, but their presence in the dict (and
    the wkt/SRID arguments they carry) is what we want to pin.
    """
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
    # Wellstick resolution, in priority order:
    #   1. LateralLine WKT from Enverus  → HEEL_TO_BH       (~99% of horizontals)
    #   2. ST_MakeLine(sh, bh) fallback  → SURFACE_TO_BH    (the rest with both endpoints)
    #   3. Neither                       → leave unset      (ON CONFLICT preserves existing)
    #
    # Strip + emptiness-check on the WKT catches whitespace-only payloads
    # that would slip past `if wkt:`. The fallback covers the well-edge
    # case that used to be handled by survey-ingest's surface→BH branch;
    # without it, the ~1% of horizontals without LateralLine would have
    # no wellstick at all after the survey pipeline is retired.
    wkt = (h.lateral_line_wkt or "").strip()
    if wkt:
        values["wellstick"] = func.ST_GeomFromText(wkt, 4326)
        values["wellstick_source"] = WellstickSource.HEEL_TO_BH.value
    elif (
        h.sh_lat is not None and h.sh_lon is not None
        and h.bh_lat is not None and h.bh_lon is not None
    ):
        values["wellstick"] = func.ST_MakeLine(
            func.ST_SetSRID(func.ST_MakePoint(h.sh_lon, h.sh_lat), 4326),
            func.ST_SetSRID(func.ST_MakePoint(h.bh_lon, h.bh_lat), 4326),
        )
        values["wellstick_source"] = WellstickSource.SURFACE_TO_BH.value
    return values


def upsert_well_headers(session: Session, headers: Iterable[WellHeader]) -> int:
    """Upsert a batch. Returns the count of rows touched.

    When the header carries a `lateral_line_wkt`, the wellstick + source
    are populated here directly — Enverus has the survey data and gives
    us the resulting LINESTRING as one column, so we don't need to run
    the heel-from-inclination pipeline ourselves. When the WKT is absent,
    wellstick is left untouched (preserves any existing survey-derived
    geometry; falls through to the survey pipeline / surface→BH fallback
    on a fresh insert).
    """
    n = 0
    now = datetime.now(timezone.utc)
    n_lateral_line = 0
    for h in headers:
        values = header_to_upsert_values(h, now)
        if "wellstick" in values:
            n_lateral_line += 1

        stmt = pg_insert(Well.__table__).values(**values)
        # ON CONFLICT (api14) DO UPDATE SET ... — every column we passed
        # this time except api14 itself. Columns NOT in `values` (e.g.
        # wellstick when LateralLine is absent) are left at their
        # existing values.
        update_cols = {
            c: stmt.excluded[c]
            for c in values
            if c not in {"api14"}
        }
        stmt = stmt.on_conflict_do_update(index_elements=["api14"], set_=update_cols)
        session.execute(stmt)
        n += 1
    session.commit()
    log.info(
        "upsert_well_headers",
        count=n,
        from_lateral_line=n_lateral_line,
    )
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
