"""DI Direct (DirectAccess) adapter — fallback for fields Prism doesn't expose.

Step 2 ships an interface-conforming stub; we don't need DI Direct for the
Loving County seed (Prism covers all required fields). Wire up the real
endpoints when the first Prism gap shows up — likely well-completion design
detail (proppant intensity buckets, fluid types) past a certain vintage.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime

from app.enverus_client.base import (
    DirectionalSurvey,
    EnverusClient,
    ProductionRecord,
    WellHeader,
)


class DIDirectClient(EnverusClient):
    def __init__(self, *, api_key: str | None) -> None:
        self._api_key = api_key

    def fetch_well_headers(
        self,
        *,
        basin: str,
        county: str | None = None,
        updated_since: datetime | None = None,
    ) -> Iterator[WellHeader]:
        raise NotImplementedError("DI Direct adapter not wired yet — use PrismClient")
        yield  # pragma: no cover  -- makes mypy treat this as a generator

    def fetch_monthly_production(
        self,
        api14_list: Iterable[str],
        *,
        start_date: date | None = None,
    ) -> Iterator[ProductionRecord]:
        raise NotImplementedError("DI Direct adapter not wired yet")
        yield  # pragma: no cover

    def fetch_directional_survey(self, api14: str) -> DirectionalSurvey | None:
        raise NotImplementedError("DI Direct adapter not wired yet")
