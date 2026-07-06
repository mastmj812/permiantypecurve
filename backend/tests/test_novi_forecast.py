"""Novi forecast warehouse-query + ingest mapping.

The warehouse query and ingest are DB-coupled; these tests pin the
column->DTO->row mapping (the part most likely to silently break on a
rename) using a tiny fake session, in the pure-function style the rest
of the suite uses. The end-to-end sync + /curves path is verified
manually against engineering_db.
"""

from __future__ import annotations

from datetime import date

from app.ingest.novi_forecast import _record_to_row
from app.warehouse_client.base import NoviForecastRecord
from app.warehouse_client.novi_forecast import fetch_novi_forecast_for_api10s


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def mappings(self):
        return iter(self._rows)


class _FakeSession:
    """Records the params it was called with; returns canned rows."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.last_params: dict | None = None

    def execute(self, _sql, params=None):
        self.last_params = params
        return _FakeResult(self._rows)


def test_fetch_maps_curated_columns_to_dto() -> None:
    rows = [
        {
            "api10": "4212345678",
            "prod_date": date(2026, 4, 1),
            "rate_calday_bopd": 252.1,
            "rate_calday_mcfd": 900.0,
            "rate_calday_bwpd": 923.0,
            "cumulative_oil_bbl": 155220,
            "cumulative_gas_mcf": 500000,
            "cumulative_water_bbl": 300000,
        }
    ]
    session = _FakeSession(rows)
    out = list(fetch_novi_forecast_for_api10s(session, ["4212345678"]))
    assert len(out) == 1
    r = out[0]
    assert isinstance(r, NoviForecastRecord)
    assert r.api10 == "4212345678"
    assert r.prod_date == date(2026, 4, 1)
    assert r.rate_calday_bopd == 252.1
    assert r.rate_calday_mcfd == 900.0
    assert r.rate_calday_bwpd == 923.0
    assert r.cumulative_oil_bbl == 155220
    assert r.cumulative_gas_mcf == 500000
    assert r.cumulative_water_bbl == 300000
    # api10 list passed through as the bound :api10s param.
    assert session.last_params == {"api10s": ["4212345678"]}


def test_fetch_empty_input_short_circuits_without_query() -> None:
    session = _FakeSession([])
    out = list(fetch_novi_forecast_for_api10s(session, []))
    assert out == []
    # No DB call attempted on empty input.
    assert session.last_params is None


def test_ingest_record_to_row_mapping() -> None:
    rec = NoviForecastRecord(
        api10="4212345678",
        prod_date=date(2026, 4, 1),
        rate_calday_bopd=252.1,
        rate_calday_mcfd=900.0,
        rate_calday_bwpd=923.0,
        cumulative_oil_bbl=155220.0,
        cumulative_gas_mcf=500000.0,
        cumulative_water_bbl=300000.0,
    )
    row = _record_to_row(rec)
    assert row == {
        "api10": "4212345678",
        "prod_date": date(2026, 4, 1),
        "rate_calday_bopd": 252.1,
        "rate_calday_mcfd": 900.0,
        "rate_calday_bwpd": 923.0,
        "cumulative_oil_bbl": 155220.0,
        "cumulative_gas_mcf": 500000.0,
        "cumulative_water_bbl": 300000.0,
    }
