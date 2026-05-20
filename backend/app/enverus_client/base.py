"""Adapter interface + DTOs.

These DTOs are the boundary between the wire format (Enverus payloads —
which we keep as raw JSONB on the well record for forensics) and the
ingest layer (which deals in typed records with clear semantics).

Keep these DTOs minimal — only fields the ingest layer actually consumes.
Anything else stays in raw_payload so we can backfill new columns later
without re-pulling from Enverus.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class WellHeader:
    api14: str
    name: str | None = None
    operator: str | None = None
    formation: str | None = None
    first_prod_date: date | None = None
    lateral_ft: float | None = None
    proppant_lbs: float | None = None
    fluid_bbl: float | None = None
    stages: int | None = None
    tvd_ft: float | None = None
    county: str | None = None
    basin: str | None = None
    status: str | None = None
    sh_lat: float | None = None
    sh_lon: float | None = None
    bh_lat: float | None = None
    bh_lon: float | None = None
    # Enverus-supplied lateral geometry as a WKT LINESTRING in EPSG:4326.
    # When present, the ingest layer uses this directly as the wellstick,
    # skipping the survey-fetch + heel-from-inclination computation
    # pipeline. ~91% coverage on horizontal Permian wells (vs ~53% via
    # our survey fetch); when missing, we fall back to the existing
    # survey or surface→BH paths.
    lateral_line_wkt: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProductionRecord:
    api14: str
    prod_date: date
    oil_bbl: float | None = None
    gas_mcf: float | None = None
    water_bbl: float | None = None
    # Float — Enverus reports fractional days for partial first months.
    producing_days: float | None = None
    source: str | None = None


class EnverusClient(ABC):
    """Adapter interface. Methods return Iterators so callers can stream
    arbitrarily large result sets without buffering.

    Survey fetch was retired in Phase 2 of the LateralLine migration —
    `LateralLine` on the wells endpoint replaces the heel-from-stations
    pipeline. The `surveys` dataset is still available upstream if a
    future need arises; just not surfaced here.
    """

    @abstractmethod
    def fetch_well_headers(
        self,
        *,
        basin: str,
        county: str | None = None,
        updated_since: datetime | None = None,
    ) -> Iterator[WellHeader]: ...

    @abstractmethod
    def fetch_monthly_production(
        self,
        api14_list: Iterable[str],
        *,
        start_date: date | None = None,
    ) -> Iterator[ProductionRecord]: ...
