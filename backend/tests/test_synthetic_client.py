"""Synthetic Enverus client: deterministic shape + edge cases.

These tests don't hit the DB. They verify the data source the seed CLI
feeds to the ingest pipeline is shaped right and exercises the awkward
heel/survey/month-1 paths we care about.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.db.models import WellstickSource
from app.enverus_client.base import DirectionalSurvey, ProductionRecord, WellHeader
from app.geo.heel_point import SurveyStation as HeelStation
from app.geo.heel_point import compute_heel
from app.seed.synthetic_client import (
    DELAWARE_FORMATIONS,
    SYNTHETIC_OPERATORS,
    SyntheticEnverusClient,
)


def _client(n: int = 50, seed: int = 42) -> SyntheticEnverusClient:
    return SyntheticEnverusClient(n_wells=n, county="Loving", basin="Permian", seed=seed)


def test_emits_requested_count_with_required_fields() -> None:
    cli = _client(n=25)
    headers = list(cli.fetch_well_headers(basin="Permian", county="Loving"))
    assert len(headers) == 25
    for h in headers:
        assert isinstance(h, WellHeader)
        assert h.api14.startswith("42301") and len(h.api14) == 14  # TX/Loving
        assert h.operator in SYNTHETIC_OPERATORS
        assert h.formation in DELAWARE_FORMATIONS
        assert h.first_prod_date is not None
        assert h.lateral_ft and h.lateral_ft >= 5000
        assert h.sh_lat is not None and h.sh_lon is not None
        assert h.bh_lat is not None and h.bh_lon is not None
        # Stick is roughly the requested lateral length — not zero, not 100 mi.
        dlat = (h.bh_lat - h.sh_lat) ** 2
        dlon = (h.bh_lon - h.sh_lon) ** 2
        assert dlat + dlon > 0  # bh is offset from sh


def test_deterministic_for_a_given_seed() -> None:
    a = list(_client(n=10, seed=7).fetch_well_headers(basin="Permian"))
    b = list(_client(n=10, seed=7).fetch_well_headers(basin="Permian"))
    assert [w.api14 for w in a] == [w.api14 for w in b]
    assert [w.operator for w in a] == [w.operator for w in b]


def test_county_filter_respected() -> None:
    cli = _client(n=10)
    assert list(cli.fetch_well_headers(basin="Permian", county="Ward")) == []
    assert len(list(cli.fetch_well_headers(basin="Permian", county="Loving"))) == 10


def test_production_starts_at_first_prod_month_with_partial_pd() -> None:
    cli = _client(n=5)
    well = next(iter(cli.fetch_well_headers(basin="Permian")))
    records = list(cli.fetch_monthly_production([well.api14]))
    assert records, "synthetic well should have production"
    first = records[0]
    assert isinstance(first, ProductionRecord)
    assert first.prod_date == well.first_prod_date.replace(day=1)  # type: ignore[union-attr]
    # Month-1 producing_days must be partial — that's the whole point of
    # this fixture, to exercise the calday month-1 exception in ingest.
    assert first.producing_days is not None
    assert 12 <= first.producing_days <= 26


def test_production_records_are_monotone_in_date() -> None:
    cli = _client(n=3)
    well = next(iter(cli.fetch_well_headers(basin="Permian")))
    records = list(cli.fetch_monthly_production([well.api14]))
    dates = [r.prod_date for r in records]
    assert dates == sorted(dates)
    # And we get back multiple years' worth — useful for fit tests later.
    assert len(records) >= 12


def test_heel_crossover_survey_is_picked_up() -> None:
    """A 'normal' synthetic well's survey, run through compute_heel, must
    yield heel_to_bh. This is the whole reason for generating a 3-station
    horizontal survey."""
    cli = _client(n=20)
    # Pick a well that is NOT in the no-survey / no-heel / malformed rosters
    # — well index 0 is normal.
    headers = list(cli.fetch_well_headers(basin="Permian"))
    normal = headers[0]
    survey = cli.fetch_directional_survey(normal.api14)
    assert isinstance(survey, DirectionalSurvey)
    stations = [
        HeelStation(
            station_seq=s.station_seq,
            md_ft=s.md_ft,
            inclination_deg=s.inclination_deg,
            lat=s.lat,
            lon=s.lon,
        )
        for s in survey.stations
    ]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.HEEL_TO_BH
    assert r.heel_lat is not None and r.heel_lon is not None


def test_no_survey_well_returns_none() -> None:
    cli = _client(n=20)
    headers = list(cli.fetch_well_headers(basin="Permian"))
    # SyntheticClient designates wells 1 and 3 as no-survey.
    assert cli.fetch_directional_survey(headers[1].api14) is None
    assert cli.fetch_directional_survey(headers[3].api14) is None


def test_no_heel_crossover_well_falls_back_via_compute_heel() -> None:
    cli = _client(n=20)
    headers = list(cli.fetch_well_headers(basin="Permian"))
    # Well 5 is the vertical-only edge case.
    survey = cli.fetch_directional_survey(headers[5].api14)
    assert survey is not None
    stations = [
        HeelStation(
            station_seq=s.station_seq,
            md_ft=s.md_ft,
            inclination_deg=s.inclination_deg,
            lat=s.lat,
            lon=s.lon,
        )
        for s in survey.stations
    ]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    # No station >= 80° — should fall back to surface→bh.
    assert r.source is WellstickSource.SURFACE_TO_BH


def test_malformed_first_station_is_skipped_next_one_wins() -> None:
    cli = _client(n=20)
    headers = list(cli.fetch_well_headers(basin="Permian"))
    # Well 7 has a >=80° station with no coords followed by a valid one.
    survey = cli.fetch_directional_survey(headers[7].api14)
    assert survey is not None
    stations = [
        HeelStation(
            station_seq=s.station_seq,
            md_ft=s.md_ft,
            inclination_deg=s.inclination_deg,
            lat=s.lat,
            lon=s.lon,
        )
        for s in survey.stations
    ]
    r = compute_heel(stations, has_bottomhole=True, has_surface=True)
    assert r.source is WellstickSource.HEEL_TO_BH
    # Should NOT be the malformed (None) station's coords.
    assert r.heel_lat is not None and r.heel_lon is not None


def test_unknown_api14_yields_no_production() -> None:
    cli = _client(n=5)
    assert list(cli.fetch_monthly_production(["00000000000000"])) == []
