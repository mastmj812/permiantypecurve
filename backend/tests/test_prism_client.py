"""Prism adapter tests — hermetic, via httpx.MockTransport.

Covers:
  * cursor pagination (two pages → merged stream)
  * retry on transient 5xx
  * 429 surfaces EnverusRateLimitError with Retry-After hint
  * 401 surfaces EnverusAuthError without retry
  * parser tolerates field-name drift (one snake_case fixture, one CamelCase)
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from app.enverus_client.exceptions import EnverusAuthError, EnverusRateLimitError
from app.enverus_client.http import EnverusHTTPClient
from app.enverus_client.prism import PrismClient


def _resp(status: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    h = headers or {}
    return httpx.Response(
        status_code=status,
        content=json.dumps(body or {}).encode(),
        headers={"content-type": "application/json", **h},
    )


def _client_with(handler) -> PrismClient:
    transport = httpx.MockTransport(handler)
    http = EnverusHTTPClient(
        base_url="https://api.enverus.test",
        api_key="test-key",
        transport=transport,
        max_retries=4,
        # Snap retry waits to ~zero so 5xx-retry test runs in <100 ms.
        retry_wait_initial=0.001,
        retry_wait_max=0.01,
        retry_wait_jitter=0.001,
    )
    return PrismClient(api_key="test-key", http=http)


def test_pagination_merges_pages() -> None:
    pages = iter(
        [
            _resp(
                200,
                {
                    "data": [
                        {"API14": "42475300010000", "ENVOperator": "Op A"},
                        {"API14": "42475300020000", "ENVOperator": "Op B"},
                    ],
                    "nextCursor": "PAGE2",
                },
            ),
            _resp(
                200,
                {
                    "data": [{"API14": "42475300030000", "ENVOperator": "Op C"}],
                    "nextCursor": None,
                },
            ),
        ]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return next(pages)

    cli = _client_with(handler)
    headers = list(cli.fetch_well_headers(basin="Permian", county="Loving"))
    assert [h.api14 for h in headers] == [
        "42475300010000",
        "42475300020000",
        "42475300030000",
    ]
    assert headers[0].operator == "Op A"


def test_field_name_drift_snake_case() -> None:
    """If Enverus ships a payload using snake_case keys, parsers should still work."""

    def handler(req: httpx.Request) -> httpx.Response:
        return _resp(
            200,
            {
                "items": [
                    {
                        "api14": "42475300010000",
                        "operator": "Op X",
                        "first_prod_date": "2022-06-15",
                        "lateral_length_ft": 10500,
                    }
                ]
            },
        )

    cli = _client_with(handler)
    headers = list(cli.fetch_well_headers(basin="Permian"))
    assert len(headers) == 1
    assert headers[0].operator == "Op X"
    assert headers[0].first_prod_date == date(2022, 6, 15)
    assert headers[0].lateral_ft == 10500.0


def test_retry_on_transient_5xx() -> None:
    state = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] < 3:
            return _resp(503, {"error": "unavailable"})
        return _resp(200, {"data": [{"API14": "42475300010000"}], "nextCursor": None})

    cli = _client_with(handler)
    headers = list(cli.fetch_well_headers(basin="Permian"))
    assert state["calls"] == 3
    assert len(headers) == 1


def test_429_surfaces_rate_limit_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _resp(429, {"error": "slow down"}, headers={"Retry-After": "2"})

    cli = _client_with(handler)
    with pytest.raises(EnverusRateLimitError) as e:
        list(cli.fetch_well_headers(basin="Permian"))
    assert e.value.retry_after_seconds is not None
    assert e.value.retry_after_seconds >= 2.0


def test_401_surfaces_auth_error_no_retry() -> None:
    state = {"calls": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return _resp(401, {"error": "expired"})

    cli = _client_with(handler)
    with pytest.raises(EnverusAuthError):
        list(cli.fetch_well_headers(basin="Permian"))
    assert state["calls"] == 1  # no retry on auth errors


def test_directional_survey_empty_returns_none() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _resp(200, {"data": [], "nextCursor": None})

    cli = _client_with(handler)
    assert cli.fetch_directional_survey("42475300010000") is None


def test_directional_survey_parses_stations_and_keeps_order() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _resp(
            200,
            {
                "data": [
                    {
                        "StationNumber": 1,
                        "MD": 0,
                        "Inclination": 0,
                        "Latitude": 31.5,
                        "Longitude": -103.5,
                    },
                    {
                        "StationNumber": 12,
                        "MD": 9000,
                        "Inclination": 88,
                        "Latitude": 31.52,
                        "Longitude": -103.48,
                    },
                ],
                "nextCursor": None,
            },
        )

    cli = _client_with(handler)
    survey = cli.fetch_directional_survey("42475300010000")
    assert survey is not None
    assert len(survey.stations) == 2
    assert survey.stations[0].station_seq == 1
    assert survey.stations[1].inclination_deg == 88
