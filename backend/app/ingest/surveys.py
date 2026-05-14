"""Upsert directional surveys; recompute heel point + wellstick on every update.

The wellstick LINESTRING is built in PostGIS via ST_MakeLine — the database
is the only place that holds both endpoints (heel/surface and bottomhole)
in geometry form.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import DirectionalSurveyStation, Well, WellstickSource
from app.enverus_client.base import DirectionalSurvey
from app.geo.heel_point import SurveyStation as HeelStation
from app.geo.heel_point import compute_heel

log = get_logger("ingest.surveys")


def upsert_survey(session: Session, survey: DirectionalSurvey) -> None:
    """Replace-all upsert: surveys are short (a few hundred stations) and
    Enverus sometimes renumbers stations between pulls. Delete-then-insert
    is simpler and avoids stale rows."""

    session.execute(
        delete(DirectionalSurveyStation).where(
            DirectionalSurveyStation.api14 == survey.api14
        )
    )

    if survey.stations:
        rows = [
            {
                "api14": survey.api14,
                "station_seq": s.station_seq,
                "md_ft": s.md_ft,
                "inclination_deg": s.inclination_deg,
                "azimuth_deg": s.azimuth_deg,
                "tvd_ft": s.tvd_ft,
                "lat": s.lat,
                "lon": s.lon,
            }
            for s in survey.stations
        ]
        session.execute(pg_insert(DirectionalSurveyStation.__table__), rows)

    _recompute_heel_and_wellstick(session, survey.api14)
    session.commit()


def _recompute_heel_and_wellstick(session: Session, api14: str) -> None:
    """Compute heel point in Python from the just-written stations, then ask
    PostGIS to materialize the wellstick LINESTRING from the canonical
    endpoints. Doing this in two steps keeps the heel rule (the part that
    has tricky edge cases) in straightforward Python with unit tests."""

    rows = session.execute(
        text(
            "SELECT station_seq, md_ft, inclination_deg, lat, lon "
            "FROM directional_surveys WHERE api14 = :api14"
        ),
        {"api14": api14},
    ).all()

    well = session.get(Well, api14)
    if well is None:
        # Survey arrived before the header. Skip — orchestrator will retry
        # after the header lands.
        log.warning("survey_before_header", api14=api14)
        return

    has_sh = session.scalar(
        text("SELECT sh_geom IS NOT NULL FROM wells WHERE api14 = :api14"),
        {"api14": api14},
    )
    has_bh = session.scalar(
        text("SELECT bh_geom IS NOT NULL FROM wells WHERE api14 = :api14"),
        {"api14": api14},
    )

    stations = [
        HeelStation(
            station_seq=r.station_seq,
            md_ft=r.md_ft,
            inclination_deg=r.inclination_deg,
            lat=r.lat,
            lon=r.lon,
        )
        for r in rows
    ]
    heel = compute_heel(
        stations,
        has_bottomhole=bool(has_bh),
        has_surface=bool(has_sh),
    )

    # Two PostGIS gotchas baked in here:
    #   1. ST_MakePoint() returns SRID 0; bh_geom/sh_geom are SRID 4326.
    #      ST_MakeLine refuses mixed-SRID inputs, so the heel point has to
    #      be tagged with ST_SetSRID(..., 4326) BEFORE entering ST_MakeLine.
    #   2. The :source parameter is used in two type contexts (enum LHS +
    #      text CASE), so each reference needs an explicit ::cast or
    #      Postgres bails with "inconsistent types deduced for parameter".
    session.execute(
        text(
            """
            UPDATE wells
            SET heel_lat = :heel_lat,
                heel_lon = :heel_lon,
                wellstick_source = (:source)::wellstick_source,
                wellstick = CASE (:source)::text
                    WHEN 'heel_to_bh' THEN
                        ST_MakeLine(
                          ST_SetSRID(ST_MakePoint(:heel_lon, :heel_lat), 4326),
                          bh_geom
                        )
                    WHEN 'surface_to_bh' THEN
                        ST_MakeLine(sh_geom, bh_geom)
                    ELSE NULL
                END,
                last_synced_at = :now
            WHERE api14 = :api14
            """
        ),
        {
            "api14": api14,
            "heel_lat": heel.heel_lat,
            "heel_lon": heel.heel_lon,
            "source": heel.source.value,
            "now": datetime.now(timezone.utc),
        },
    )
    log.info(
        "wellstick_recomputed",
        api14=api14,
        source=heel.source.value,
        stations=len(stations),
    )


def force_recompute_wellstick(session: Session, api14: str) -> WellstickSource:
    """Public hook — useful if the well header changes (new SH/BH) and we
    need the wellstick re-built without re-ingesting the survey."""
    _recompute_heel_and_wellstick(session, api14)
    session.commit()
    src: WellstickSource | None = session.scalar(
        text("SELECT wellstick_source FROM wells WHERE api14 = :api14"), {"api14": api14}
    )
    return src or WellstickSource.NONE
