"""Water-provenance sync passthrough — DB-free unit tests.

``curated.water_data_quality`` (one row per api10) flags whether a
well's public water series is measured or a vendor formula (a static
WOR x oil — 'calculated'). The app syncs ``water_source`` + ``wor_cv``
onto ``wells`` through the standard header-sync path. CONVENTION OF
RECORD (2026-08-17): FLAG ONLY — badge and filter; nothing is
auto-excluded from any fit or cohort. These tests lock the passthrough
so a refactor can't silently drop the columns from the sync.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.ingest.wells import header_to_upsert_values
from app.warehouse_client.base import WellHeader
from app.warehouse_client.wells import (
    _FETCH_ONE_SQL,
    _HEADER_COLUMNS_SQL,
    _row_to_dto,
)


def _full_row(**overrides: object) -> dict[str, object]:
    """Every alias _row_to_dto reads, minimally populated."""
    row: dict[str, object] = {
        "api10": "4200000001",
        "api14": None,
        "name": None,
        "operator": None,
        "formation": None,
        "formation_blueox": None,
        "basin_blueox": None,
        "first_prod_date": None,
        "lateral_ft": None,
        "proppant_lbs": None,
        "fluid_bbl": None,
        "tvd_ft": None,
        "county": None,
        "basin": None,
        "subbasin": None,
        "status_raw": "Active",
        "sh_lat": None,
        "sh_lon": None,
        "bh_lat": None,
        "bh_lon": None,
        "wellstick_wkt": None,
        "vintage_year": None,
        "completion_vintage_bucket": None,
        "is_horizontal": True,
        "directional_survey_is_planned": None,
        "novi_oil_eur": None,
        "lateral_closer_xy_ft": None,
        "water_source": None,
        "wor_cv": None,
    }
    row.update(overrides)
    return row


def test_row_to_dto_maps_water_provenance() -> None:
    dto = _row_to_dto(_full_row(water_source="calculated", wor_cv=0.0123))
    assert dto.water_source == "calculated"
    assert dto.wor_cv == 0.0123


def test_row_to_dto_water_provenance_nullable() -> None:
    # Wells absent from curated.water_data_quality (LEFT JOIN miss —
    # no producing months) carry NULLs, not a fabricated class.
    dto = _row_to_dto(_full_row())
    assert dto.water_source is None
    assert dto.wor_cv is None


def test_upsert_values_pass_water_provenance_through() -> None:
    h = WellHeader(api10="4200000001", water_source="measured", wor_cv=1.85)
    values = header_to_upsert_values(h, datetime.now(UTC))
    assert values["water_source"] == "measured"
    assert values["wor_cv"] == 1.85


def test_both_fetch_sql_paths_join_water_data_quality() -> None:
    # Single-well and bulk fetch share _HEADER_COLUMNS_SQL, but each
    # builds its own FROM clause — both must join the matview AND select
    # the columns, or synced wells silently lose the flag.
    single = str(_FETCH_ONE_SQL)
    assert "curated.water_data_quality" in single
    assert "wdq.water_source" in single
    assert "wdq.wor_cv" in single
    assert "wdq.water_source" in _HEADER_COLUMNS_SQL
    assert "wdq.wor_cv" in _HEADER_COLUMNS_SQL
    # The bulk fetcher assembles its FROM clause inline — check the
    # source so the join can't be dropped there while the column block
    # still references the wdq alias (which would be a runtime error
    # only the nightly sync would hit).
    import inspect

    from app.warehouse_client.wells import fetch_well_headers

    assert "curated.water_data_quality wdq" in inspect.getsource(fetch_well_headers)
