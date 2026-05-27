"""Prism adapter — thin wrapper over the Enverus Developer API v3 SDK.

The SDK handles HTTP, OAuth token refresh, pagination, and 429 backoff
for us. Our job is reduced to:
  * passing the right dataset name + filter kwargs to `sdk.query(...)`
  * mapping each returned dict into our typed DTO via `_parse_*`

Dataset names confirmed against the user's subscription:
  * `wells`                       → well headers
  * `production`                  → monthly production volumes
  * `full-directional-surveys`    → directional survey stations

If Enverus renames fields between versions, the parser functions are the
narrow boundary that needs updating. Endpoint paths, auth, pagination,
and retry logic are not our concern — the SDK owns them.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime
from typing import Any

from app.core.logging import get_logger
from app.enverus_client.base import (
    EnverusClient,
    ProductionRecord,
    WellHeader,
)

log = get_logger("enverus.prism")

DATASET_WELLS = "wells"
DATASET_PRODUCTION = "production"

# Chunk size for the production endpoint when filtering by an api14 list —
# the SDK paginates within a single query, but very long filter strings
# can trip URL-length limits on intermediary proxies.
_API14_CHUNK_SIZE = 50

# Enverus' `ENVBasin` column stores Permian wells under three sub-basin
# names rather than a single "PERMIAN" umbrella. To honor our app-level
# basin="Permian" request we have to filter on all three. The mapping
# stays inside the adapter — the rest of the codebase still thinks of
# "Permian" as one basin.
_BASIN_VALUE_MAP: dict[str, tuple[str, ...]] = {
    "PERMIAN": ("DELAWARE", "MIDLAND", "PERMIAN OTHER"),
    # Add other plays as they come online (Eagle Ford, Bakken, etc.).
}


def _enverus_basin_filter(app_basin: str) -> str:
    """Resolve the app-level basin name to the Enverus filter value.

    Uses Enverus' comma-separated IN-style filter when multiple sub-basin
    values map to one app-level name; otherwise returns the single value.
    """
    key = app_basin.upper()
    values = _BASIN_VALUE_MAP.get(key, (key,))
    return ",".join(values)


class PrismClient(EnverusClient):
    """Wraps Enverus' DeveloperAPIv3 SDK behind our `EnverusClient` ABC.

    The SDK is lazy-initialized on first use so callers can construct a
    PrismClient with `api_key=None` for synthetic-only flows without
    importing the SDK or hitting the network.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        sdk_client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        # Pre-wired SDK client (used in tests with a fake/mocked SDK).
        self._sdk_override = sdk_client
        self._sdk: Any | None = sdk_client

    def _get_sdk(self) -> Any:
        if self._sdk is not None:
            return self._sdk
        if not self._api_key:
            raise RuntimeError(
                "PrismClient called without an api_key — set "
                "ENVERUS_API_KEY_PRISM in .env or use synthetic seeding."
            )
        from enverus_developer_api import DeveloperAPIv3  # lazy import

        self._sdk = DeveloperAPIv3(secret_key=self._api_key)
        return self._sdk

    # -------------------- well headers --------------------
    def fetch_well_headers(
        self,
        *,
        basin: str,
        county: str | None = None,
        updated_since: datetime | None = None,
        first_prod_after: date | None = None,
        min_lateral_ft: float | None = None,
    ) -> Iterator[WellHeader]:
        # Enverus stores ENVBasin VALUES uppercase AND at sub-basin level
        # (DELAWARE / MIDLAND / PERMIAN OTHER). Our app talks in app-level
        # basin names ("Permian"); `_enverus_basin_filter` maps and joins.
        # Note: Country IS a returned column but NOT a filterable one on
        # the wells dataset — adding `Country=US` zeroes the result. The
        # basin filter alone restricts to US wells anyway.
        filters: dict[str, Any] = {"ENVBasin": _enverus_basin_filter(basin)}
        if county is not None:
            filters["county"] = county.upper()
        if updated_since is not None:
            filters["updateddate"] = f"gt({updated_since.isoformat()})"
        # Server-side scope filters — push as much filtering as we can
        # onto Enverus so we don't pay network + DB cost for wells the
        # type-curve tool will never use. The vast majority of pre-2000
        # wells in Ward/Winkler are conventional verticals, and a
        # lateral-length floor catches the rare post-2000 vertical that
        # would otherwise slip through.
        if first_prod_after is not None:
            filters["FirstProdDate"] = f"gt({first_prod_after.isoformat()})"
        if min_lateral_ft is not None:
            # Enverus exposes lateral length under multiple aliases
            # (LateralLength_FT in raw, LateralLength in some derived
            # views). `LateralLength_FT` is the documented PascalCase
            # column on the wells dataset.
            filters["LateralLength_FT"] = f"gt({min_lateral_ft})"
        # `deleteddate=null` excludes wells logically deleted since their
        # last appearance — standard practice for incremental syncs.
        filters.setdefault("deleteddate", "null")

        for row in self._get_sdk().query(DATASET_WELLS, **filters):
            yield _parse_well_header(row)

    # -------------------- monthly production --------------------
    def fetch_monthly_production(
        self,
        api14_list: Iterable[str],
        *,
        start_date: date | None = None,
    ) -> Iterator[ProductionRecord]:
        # The production and surveys datasets use Enverus' canonical
        # `API_UWI_14_Unformatted` column for the 14-digit api14. The
        # wells dataset has both that and a convenience `API14` alias —
        # production / surveys only know the full name.
        api14s = list(api14_list)
        for i in range(0, len(api14s), _API14_CHUNK_SIZE):
            chunk = api14s[i : i + _API14_CHUNK_SIZE]
            filters: dict[str, Any] = {"API_UWI_14_Unformatted": ",".join(chunk)}
            if start_date is not None:
                filters["producingmonth"] = f"gte({start_date.isoformat()})"
            for row in self._get_sdk().query(DATASET_PRODUCTION, **filters):
                yield _parse_production_record(row)

    # -------------------- directional survey --------------------
    # The survey dataset is keyed on api12 (wellbore), not api14 (well +
    # recompletion). A directional survey is a property of the borehole,
    # so recompletions (api14 differs in the trailing 2 digits) share it.

# ---------------- parsers (the boundary that's most likely to drift) ----------------
# Each parser is forgiving: a missing/None field becomes None. Update these
# when Enverus renames a column or you discover an alternate spelling in
# the wild. Keys are checked in order; the first non-None wins.

def _g(d: dict[str, Any], *keys: str) -> Any:
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
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _parse_well_header(item: dict[str, Any]) -> WellHeader:
    # Enverus wells dataset column names use `<Name>_<Unit>` suffixes.
    # `_BH` suffix marks the bottomhole point; surface lat/lon are plain
    # `Latitude` / `Longitude`. County is stored uppercase ("LOVING") —
    # we title-case it on the way in so UI strings stay consistent with
    # what the user types ("Loving" in the filter panel, etc).
    raw_county = _g(item, "County", "county")
    county = raw_county.title() if isinstance(raw_county, str) else raw_county
    return WellHeader(
        api14=str(_g(item, "API_UWI_14_Unformatted", "API14", "api14", "API_14")),
        # ENVWellName is the Enverus-normalized lease/well name; WellName is
        # the operator-reported variant. Either is fine — the field is
        # display-only and there's no canonical authority.
        name=_g(item, "ENVWellName", "WellName", "well_name", "LeaseName"),
        operator=_g(item, "ENVOperator", "operator", "operator_name"),
        formation=_g(item, "ENVInterval", "Formation", "formation", "target_formation"),
        first_prod_date=_as_date(_g(item, "FirstProdDate", "firstProdDate", "first_prod_date")),
        lateral_ft=_as_float(_g(item, "LateralLength_FT", "LateralLength", "lateralFt")),
        proppant_lbs=_as_float(_g(item, "Proppant_LBS", "Proppant", "proppantLbs", "totalProppant")),
        fluid_bbl=_as_float(_g(item, "TotalFluidPumped_BBL", "Fluid", "fluidBbl", "totalFluid")),
        stages=_as_int(_g(item, "FracStages", "StimulatedStages", "Stages", "stageCount")),
        tvd_ft=_as_float(_g(item, "TVD_FT", "TVD", "trueVerticalDepth")),
        county=county,
        basin=_g(item, "ENVBasin", "basin"),
        status=_g(item, "ENVWellStatus", "status", "well_status"),
        sh_lat=_as_float(_g(item, "Latitude", "surfaceLatitude", "sh_lat")),
        sh_lon=_as_float(_g(item, "Longitude", "surfaceLongitude", "sh_lon")),
        bh_lat=_as_float(_g(item, "Latitude_BH", "BottomLatitude", "bottomholeLatitude", "bh_lat")),
        bh_lon=_as_float(_g(item, "Longitude_BH", "BottomLongitude", "bottomholeLongitude", "bh_lon")),
        # Enverus delivers `LateralLine` as a WKT LINESTRING in EPSG:4326
        # already — same SRID and shape we'd build internally via
        # ST_MakeLine(heel, BH). When non-empty, ingest uses this as the
        # wellstick directly instead of fetching the survey + computing
        # a heel point. Strings only; the WKB hex fallback Enverus emits
        # for some other geom columns isn't seen on this field, but we
        # defensively reject non-string values rather than guessing.
        lateral_line_wkt=_as_lateral_line_wkt(
            _g(item, "LateralLine", "lateralLine")
        ),
        raw=item,
    )


def _as_lateral_line_wkt(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    stripped = v.strip()
    # Enverus occasionally emits empty strings or whitespace for wells
    # where the lateral geometry isn't populated; treat as missing.
    if not stripped:
        return None
    # Cheap shape sanity check — anything else gets dropped so a bad row
    # can't break the ingest. The PostGIS layer will reject malformed WKT
    # on insert as a second guard.
    if not stripped.upper().startswith("LINESTRING"):
        return None
    return stripped


def _parse_production_record(item: dict[str, Any]) -> ProductionRecord:
    return ProductionRecord(
        api14=str(_g(item, "API_UWI_14_Unformatted", "API14", "api14")),
        prod_date=_as_date(_g(item, "ProducingMonth", "prodDate", "month", "producingmonth"))
        or date(1900, 1, 1),
        oil_bbl=_as_float(_g(item, "LiquidsProd_BBL", "oil_bbl", "oilBbl", "liquidsprod_bbl")),
        gas_mcf=_as_float(_g(item, "GasProd_MCF", "gas_mcf", "gasMcf", "gasprod_mcf")),
        water_bbl=_as_float(_g(item, "WaterProd_BBL", "water_bbl", "waterBbl", "waterprod_bbl")),
        # Float — Enverus reports fractional producing-days for partial
        # first months (e.g. 0.166 = ~4 hours online on first-prod day).
        producing_days=_as_float(
            _g(item, "ProducingDays", "producing_days", "producingDays", "producingdays")
        ),
        source="prism",
    )
