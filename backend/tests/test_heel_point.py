"""Heel-point detection rules — pure function, no DB."""

from __future__ import annotations

from app.db.models import WellstickSource
from app.geo.heel_point import SurveyStation, compute_heel


def s(seq: int, md: float, inc: float, lat: float | None = 32.0, lon: float | None = -103.0) -> SurveyStation:
    return SurveyStation(station_seq=seq, md_ft=md, inclination_deg=inc, lat=lat, lon=lon)


def test_first_station_above_threshold_becomes_heel() -> None:
    stations = [s(1, 100, 5), s(2, 5000, 45), s(3, 8000, 82.5), s(4, 10000, 89)]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.HEEL_TO_BH
    # Station 3 is the first ≥ 80°; station 4 must NOT win even though it's higher.
    assert (r.heel_lat, r.heel_lon) == (32.0, -103.0)


def test_md_sort_order_is_respected() -> None:
    # Stations given out-of-order; function must sort by MD before scanning.
    stations = [
        s(3, 10000, 89, lat=99.0, lon=99.0),
        s(1, 8000, 82.5, lat=10.0, lon=10.0),
        s(2, 12000, 90),
    ]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.HEEL_TO_BH
    assert (r.heel_lat, r.heel_lon) == (10.0, 10.0)


def test_exactly_80_degrees_qualifies() -> None:
    stations = [s(1, 100, 5), s(2, 5000, 80.0)]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.HEEL_TO_BH


def test_no_high_inclination_station_falls_back_to_surface_to_bh() -> None:
    stations = [s(1, 100, 5), s(2, 5000, 45), s(3, 6000, 60)]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.SURFACE_TO_BH
    assert r.heel_lat is None and r.heel_lon is None


def test_no_survey_with_sh_and_bh_uses_surface_to_bh() -> None:
    r = compute_heel([], has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.SURFACE_TO_BH


def test_no_survey_no_bottomhole_yields_none() -> None:
    r = compute_heel([], has_bottomhole=False, has_surface=True)
    assert r.source is WellstickSource.NONE
    assert r.heel_lat is None


def test_station_with_no_coords_is_skipped_not_used() -> None:
    """A high-incl station with missing lat/lon must NOT be picked — there's
    nothing usable for the LINESTRING. Keep scanning for a later valid one."""
    stations = [s(1, 8000, 82.5, lat=None, lon=None), s(2, 9000, 88, lat=33.0, lon=-104.0)]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.HEEL_TO_BH
    assert (r.heel_lat, r.heel_lon) == (33.0, -104.0)


def test_only_high_incl_stations_lack_coords_falls_back() -> None:
    """If *every* candidate heel lacks coords, surface→bh kicks in."""
    stations = [s(1, 8000, 82.5, lat=None, lon=None), s(2, 9000, 88, lat=None, lon=None)]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.SURFACE_TO_BH
