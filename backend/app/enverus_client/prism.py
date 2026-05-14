"""Prism (Direct Access REST) adapter — primary Enverus source.

NOTE on endpoint paths and field names: these are placeholders modeled on
the public Prism Direct Access v3 patterns. Verify each against the live
contract before the first non-mock pull — Enverus has been known to rename
fields between minor versions. The boundary is narrow on purpose: changes
land in `_parse_*` and the `_PATH_*` constants only.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime
from typing import Any

from app.core.logging import get_logger
from app.enverus_client.base import (
    DirectionalSurvey,
    EnverusClient,
    ProductionRecord,
    SurveyStation,
    WellHeader,
)
from app.enverus_client.http import EnverusHTTPClient

log = get_logger("enverus.prism")

# --- API surface (verify against current Prism docs before going live) ---
DEFAULT_BASE_URL = "https://api.enverus.com"
_PATH_WELLS = "/v3/direct-access/wells"
_PATH_PRODUCTION = "/v3/direct-access/producing-entity-monthly-production"
_PATH_SURVEY = "/v3/direct-access/wellbores-directional-surveys"

_DEFAULT_PAGE_SIZE = 500


class PrismClient(EnverusClient):
    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = DEFAULT_BASE_URL,
        http: EnverusHTTPClient | None = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> None:
        self._page_size = page_size
        # `http` injection is what lets tests use httpx.MockTransport.
        self._http = http or EnverusHTTPClient(base_url=base_url, api_key=api_key)

    def close(self) -> None:
        self._http.close()

    # -------------------- well headers --------------------
    def fetch_well_headers(
        self,
        *,
        basin: str,
        county: str | None = None,
        updated_since: datetime | None = None,
    ) -> Iterator[WellHeader]:
        params: dict[str, Any] = {"basin": basin, "pageSize": self._page_size}
        if county is not None:
            params["county"] = county
        if updated_since is not None:
            params["updatedSince"] = updated_since.isoformat()

        for item in self._paginate(_PATH_WELLS, params):
            yield _parse_well_header(item)

    # -------------------- monthly production --------------------
    def fetch_monthly_production(
        self,
        api14_list: Iterable[str],
        *,
        start_date: date | None = None,
    ) -> Iterator[ProductionRecord]:
        # Prism accepts a comma-delimited filter; we chunk to keep query
        # strings under typical proxy limits (8 KB).
        chunk_size = 50
        api14s = list(api14_list)
        for i in range(0, len(api14s), chunk_size):
            chunk = api14s[i : i + chunk_size]
            params: dict[str, Any] = {
                "api14": ",".join(chunk),
                "pageSize": self._page_size,
            }
            if start_date is not None:
                params["startDate"] = start_date.isoformat()
            for item in self._paginate(_PATH_PRODUCTION, params):
                yield _parse_production_record(item)

    # -------------------- directional survey --------------------
    def fetch_directional_survey(self, api14: str) -> DirectionalSurvey | None:
        params = {"api14": api14, "pageSize": self._page_size}
        stations: list[SurveyStation] = []
        for item in self._paginate(_PATH_SURVEY, params):
            stations.append(_parse_station(item))
        if not stations:
            return None
        return DirectionalSurvey(api14=api14, stations=stations)

    # -------------------- pagination --------------------
    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        page = 0
        while True:
            q = {**params}
            if cursor:
                q["cursor"] = cursor
            payload = self._http.get(path, params=q)
            items = payload.get("data") or payload.get("items") or []
            for item in items:
                yield item
            page += 1
            # Prism uses `nextCursor`; older endpoints expose `next`. Accept either.
            cursor = payload.get("nextCursor") or payload.get("next")
            if not cursor:
                return
            log.debug("prism_paginate", path=path, page=page, cursor=cursor)


# ---------------- parsers (the boundary that's most likely to drift) ----------------
# Each parser is forgiving: a missing/None field becomes None. Keep the
# `_get` helpers central so renaming a field is a one-line change.

def _g(d: dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value from any of the candidate keys."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _as_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _as_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        # Accept full ISO datetimes and date-only strings.
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _parse_well_header(item: dict[str, Any]) -> WellHeader:
    return WellHeader(
        api14=str(_g(item, "API14", "api14", "API_14")),
        operator=_g(item, "ENVOperator", "operator", "operator_name"),
        formation=_g(item, "ENVInterval", "formation", "target_formation"),
        first_prod_date=_as_date(_g(item, "FirstProdDate", "firstProdDate", "first_prod_date")),
        lateral_ft=_as_float(_g(item, "LateralLength", "lateral_length_ft", "lateralFt")),
        proppant_lbs=_as_float(_g(item, "Proppant", "proppantLbs", "totalProppant")),
        fluid_bbl=_as_float(_g(item, "Fluid", "fluidBbl", "totalFluid")),
        stages=_as_int(_g(item, "Stages", "stageCount", "stages")),
        tvd_ft=_as_float(_g(item, "TVD", "tvd_ft", "trueVerticalDepth")),
        county=_g(item, "County", "county"),
        basin=_g(item, "ENVBasin", "basin"),
        status=_g(item, "ENVWellStatus", "status", "well_status"),
        sh_lat=_as_float(_g(item, "Latitude", "surfaceLatitude", "sh_lat")),
        sh_lon=_as_float(_g(item, "Longitude", "surfaceLongitude", "sh_lon")),
        bh_lat=_as_float(_g(item, "BottomLatitude", "bottomholeLatitude", "bh_lat")),
        bh_lon=_as_float(_g(item, "BottomLongitude", "bottomholeLongitude", "bh_lon")),
        raw=item,
    )


def _parse_production_record(item: dict[str, Any]) -> ProductionRecord:
    return ProductionRecord(
        api14=str(_g(item, "API14", "api14")),
        prod_date=_as_date(_g(item, "ProducingMonth", "prodDate", "month")) or date(1900, 1, 1),
        oil_bbl=_as_float(_g(item, "LiquidsProd_BBL", "oil_bbl", "oilBbl")),
        gas_mcf=_as_float(_g(item, "GasProd_MCF", "gas_mcf", "gasMcf")),
        water_bbl=_as_float(_g(item, "WaterProd_BBL", "water_bbl", "waterBbl")),
        producing_days=_as_int(_g(item, "ProducingDays", "producing_days", "producingDays")),
        source="prism",
    )


def _parse_station(item: dict[str, Any]) -> SurveyStation:
    return SurveyStation(
        station_seq=_as_int(_g(item, "StationNumber", "stationSeq", "station_seq")) or 0,
        md_ft=_as_float(_g(item, "MD", "md_ft", "measuredDepth")) or 0.0,
        inclination_deg=_as_float(_g(item, "Inclination", "inclination_deg")) or 0.0,
        azimuth_deg=_as_float(_g(item, "Azimuth", "azimuth_deg")),
        tvd_ft=_as_float(_g(item, "TVD", "tvd_ft")),
        lat=_as_float(_g(item, "Latitude", "lat")),
        lon=_as_float(_g(item, "Longitude", "lon")),
    )
