"""Prism adapter tests after the SDK swap.

The SDK now owns HTTP, OAuth refresh, pagination, and 429 backoff —
those tests are retired. What remains is OUR responsibility:
  * dataset name + filter kwargs are passed correctly through to SDK.query()
  * the parser functions tolerate Enverus' field-name variants
  * iterator semantics (lazy yielding, chunked production calls) behave

We inject a `FakeSDK` with a `query(dataset, **filters)` method that
returns canned dicts. No real network involved.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from app.enverus_client.prism import (
    DATASET_PRODUCTION,
    DATASET_SURVEYS,
    DATASET_WELLS,
    PrismClient,
)


class FakeSDK:
    """Captures every query() call and replays a queued response."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, list[list[dict[str, Any]]]] = {}

    def enqueue(self, dataset: str, rows: list[dict[str, Any]]) -> None:
        self.responses.setdefault(dataset, []).append(rows)

    def query(self, dataset: str, **filters: Any):
        self.calls.append((dataset, filters))
        rows = self.responses.get(dataset, [[]])
        if rows:
            yield from rows.pop(0)


def _cli(fake: FakeSDK) -> PrismClient:
    return PrismClient(api_key=None, sdk_client=fake)


# ============================ well headers ============================


def test_well_header_filters_passed_through_with_default_deleteddate() -> None:
    fake = FakeSDK()
    fake.enqueue(DATASET_WELLS, [
        {"API_UWI_14_Unformatted": "42301378430000", "ENVOperator": "BP"},
    ])
    list(_cli(fake).fetch_well_headers(basin="Permian", county="Loving"))
    dataset, filters = fake.calls[0]
    assert dataset == DATASET_WELLS
    # ENVBasin is sub-basin-level in Enverus: Permian splits into
    # DELAWARE / MIDLAND / PERMIAN OTHER. We join with commas so the
    # SDK passes them as an IN-style filter.
    assert filters["ENVBasin"] == "DELAWARE,MIDLAND,PERMIAN OTHER"
    # County stored uppercase by Enverus.
    assert filters["county"] == "LOVING"
    assert filters["deleteddate"] == "null"
    # Country is NOT a filterable column on the wells dataset; sending
    # it zeroes the result. We rely on the basin filter for geography.
    assert "Country" not in filters


def test_well_header_updated_since_uses_gt_filter() -> None:
    fake = FakeSDK()
    fake.enqueue(DATASET_WELLS, [])
    cutoff = datetime(2026, 1, 15, 12, 0, 0)
    list(_cli(fake).fetch_well_headers(basin="Permian", updated_since=cutoff))
    _, filters = fake.calls[0]
    assert filters["updateddate"].startswith("gt(")
    assert "2026-01-15" in filters["updateddate"]


def test_well_header_parser_maps_enverus_column_names() -> None:
    """Real Enverus column names with _FT / _LBS / _BBL / _BH suffixes
    and uppercase county. Parser should pull everything correctly and
    title-case the county on the way through."""
    enverus_row = {
        "API_UWI_14_Unformatted": "42475300010000",
        "ENVOperator": "OXY USA INC.",
        "ENVInterval": "WOLFCAMP A LOWER",
        "FirstProdDate": "2022-06-15T00:00:00",
        "LateralLength_FT": 10500,
        "Proppant_LBS": 22_000_000,
        "TotalFluidPumped_BBL": 420_000,
        "FracStages": 48,
        "TVD_FT": 11_000,
        "County": "LOVING",       # uppercase as Enverus stores it
        "ENVBasin": "PERMIAN",
        "ENVWellStatus": "PRODUCING",
        "Latitude": 31.8, "Longitude": -103.7,
        "Latitude_BH": 31.81, "Longitude_BH": -103.72,
    }
    fake = FakeSDK()
    fake.enqueue(DATASET_WELLS, [enverus_row])
    w = list(_cli(fake).fetch_well_headers(basin="Permian"))[0]

    assert w.api14 == "42475300010000"
    assert w.operator == "OXY USA INC."
    assert w.formation == "WOLFCAMP A LOWER"
    assert w.first_prod_date == date(2022, 6, 15)
    assert w.lateral_ft == 10500.0
    assert w.proppant_lbs == 22_000_000.0
    assert w.fluid_bbl == 420_000.0
    assert w.stages == 48
    assert w.tvd_ft == 11_000.0
    # County normalized to title case for UI consistency.
    assert w.county == "Loving"
    assert w.basin == "PERMIAN"
    assert w.status == "PRODUCING"
    assert w.sh_lat == 31.8 and w.sh_lon == -103.7
    assert w.bh_lat == 31.81 and w.bh_lon == -103.72


def test_well_header_legacy_snake_case_fallback_still_works() -> None:
    """The legacy field-name candidates in `_g()` calls should still
    work — we keep them as a defensive fallback for any non-standard
    source (manual imports, future SDK changes, etc.)."""
    snake = {
        "api14": "42475300010000", "operator": "Op A",
        "formation": "Wolfcamp A",
        "county": "loving",  # not from Enverus; tests title-casing edge
    }
    fake = FakeSDK()
    fake.enqueue(DATASET_WELLS, [snake])
    w = list(_cli(fake).fetch_well_headers(basin="Permian"))[0]
    assert w.api14 == "42475300010000"
    assert w.operator == "Op A"
    assert w.formation == "Wolfcamp A"
    assert w.county == "Loving"


# ============================ production ============================


def test_production_chunks_api14_list_into_groups_of_50() -> None:
    fake = FakeSDK()
    api14s = [f"42475{i:09d}" for i in range(120)]
    for _ in range(3):
        fake.enqueue(DATASET_PRODUCTION, [])
    list(_cli(fake).fetch_monthly_production(api14s))
    # 120 wells / 50 per chunk = 3 calls.
    assert len(fake.calls) == 3
    for _, filters in fake.calls:
        # Production / surveys datasets use Enverus' canonical column name,
        # not the lowercase alias the wells dataset accepts.
        assert "API_UWI_14_Unformatted" in filters
        assert filters["API_UWI_14_Unformatted"].count(",") <= 49


def test_production_start_date_uses_gte_filter() -> None:
    fake = FakeSDK()
    fake.enqueue(DATASET_PRODUCTION, [])
    list(_cli(fake).fetch_monthly_production(["42475300010000"], start_date=date(2024, 1, 1)))
    _, filters = fake.calls[0]
    assert filters["producingmonth"].startswith("gte(")
    assert "2024-01-01" in filters["producingmonth"]


def test_production_parser_maps_known_columns() -> None:
    fake = FakeSDK()
    fake.enqueue(DATASET_PRODUCTION, [{
        "API14": "42475300010000",
        "ProducingMonth": "2023-06-01",
        "LiquidsProd_BBL": 12500,
        "GasProd_MCF": 95000,
        "WaterProd_BBL": 8700,
        "ProducingDays": 30,
    }])
    rec = next(_cli(fake).fetch_monthly_production(["42475300010000"]))
    assert rec.api14 == "42475300010000"
    assert rec.prod_date == date(2023, 6, 1)
    assert rec.oil_bbl == 12500.0
    assert rec.gas_mcf == 95000.0
    assert rec.water_bbl == 8700.0
    assert rec.producing_days == 30
    assert rec.source == "prism"


# ============================ surveys ============================


def test_survey_empty_response_returns_none() -> None:
    fake = FakeSDK()
    fake.enqueue(DATASET_SURVEYS, [])
    assert _cli(fake).fetch_directional_survey("42475300010000") is None


def test_survey_filter_uses_api12_not_api14() -> None:
    """Survey dataset is keyed on api12 (wellbore). We truncate the
    last 2 digits (recompletion suffix) before querying."""
    fake = FakeSDK()
    fake.enqueue(DATASET_SURVEYS, [])
    _cli(fake).fetch_directional_survey("42475300010000")  # 14 digits
    assert fake.calls[0][0] == DATASET_SURVEYS
    assert fake.calls[0][1]["API_UWI_12_Unformatted"] == "424753000100"


def test_survey_batch_chunks_by_api12_and_groups_stations() -> None:
    """Batched survey fetch sends one query per CHUNK of api12s and
    splits the interleaved response back by api12. Avoids the 429 we
    saw on per-well calls."""
    fake = FakeSDK()
    # Two api12s in the response; stations interleaved.
    fake.enqueue(DATASET_SURVEYS, [
        {"StationNumber": 1, "API_UWI_12_Unformatted": "424753000100",
         "MeasuredDepth_FT": 0, "Inclination_DEG": 0,
         "Latitude": 31.5, "Longitude": -103.5},
        {"StationNumber": 1, "API_UWI_12_Unformatted": "424753000200",
         "MeasuredDepth_FT": 0, "Inclination_DEG": 0,
         "Latitude": 31.6, "Longitude": -103.4},
        {"StationNumber": 2, "API_UWI_12_Unformatted": "424753000100",
         "MeasuredDepth_FT": 9000, "Inclination_DEG": 88,
         "Latitude": 31.52, "Longitude": -103.48},
    ])
    surveys = list(_cli(fake).fetch_directional_surveys([
        "42475300010000", "42475300020000",
    ]))
    by_api14 = {s.api14: s for s in surveys}
    assert set(by_api14.keys()) == {"42475300010000", "42475300020000"}
    assert len(by_api14["42475300010000"].stations) == 2
    # Stations re-sorted by station_seq even though they arrived interleaved.
    assert [s.station_seq for s in by_api14["42475300010000"].stations] == [1, 2]
    assert len(by_api14["42475300020000"].stations) == 1
    # One API call covered both wells.
    assert len(fake.calls) == 1
    dataset, filters = fake.calls[0]
    assert dataset == DATASET_SURVEYS
    assert filters["API_UWI_12_Unformatted"] == "424753000100,424753000200"


def test_survey_parses_enverus_unit_suffixed_columns() -> None:
    """Surveys come back with names like MeasuredDepth_FT / Inclination_DEG
    — parser must pick those up (and the response carries no api14)."""
    fake = FakeSDK()
    fake.enqueue(DATASET_SURVEYS, [
        {"StationNumber": 1, "MeasuredDepth_FT": 0, "Inclination_DEG": 0,
         "Azimuth_DEG": 90, "TVD_FT": 0,
         "Latitude": 31.5, "Longitude": -103.5},
        {"StationNumber": 12, "MeasuredDepth_FT": 9000, "Inclination_DEG": 88,
         "Azimuth_DEG": 92, "TVD_FT": 8800,
         "Latitude": 31.52, "Longitude": -103.48},
    ])
    survey = _cli(fake).fetch_directional_survey("42475300010000")
    assert survey is not None
    assert survey.api14 == "42475300010000"  # preserved on the way back out
    assert len(survey.stations) == 2
    assert survey.stations[0].md_ft == 0
    assert survey.stations[1].md_ft == 9000
    assert survey.stations[1].inclination_deg == 88
    assert survey.stations[1].azimuth_deg == 92
    assert survey.stations[1].tvd_ft == 8800


# ============================ guardrails ============================


def test_no_sdk_call_when_api_key_missing_and_no_override() -> None:
    cli = PrismClient(api_key=None)
    with pytest.raises(RuntimeError, match="ENVERUS_API_KEY_PRISM"):
        next(cli.fetch_well_headers(basin="Permian"))
