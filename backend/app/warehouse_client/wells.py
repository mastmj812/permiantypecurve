"""Fetch well headers from ``curated.wells_enriched``.

Replaces ``enverus_client.PrismClient.fetch_well_headers`` post-cutover.
Reads from engineering_db; emits api10-keyed ``WellHeader`` DTOs.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.warehouse_client.base import WellHeader

# Mapping from Novi's well_status vocabulary (as it appears in
# curated.wells.well_status) to the app's existing WellStatus enum.
# Derived empirically from the full fetch scope (horizontal, 2010+
# completion or NULL-completion non-permit; 2026-08 census: Active
# 57,805 / Spud 2,260 / Inactive 1,974 / P&A 1,569 / Completed 1,351 /
# DUC 654 / Abandoned 601 — Spud/DUC are almost entirely the
# NULL-completion wellbores). Any value not in this map collapses
# to UNKNOWN — surfacing a new Novi status code via the audit query
# is the trigger to add a mapping.
_STATUS_REMAP: dict[str, str] = {
    "Active": "PDP",
    "Completed": "PDP",
    "Inactive": "INACTIVE",
    "P&A": "PA",
    "Abandoned": "PA",
    "Spud": "DUC",
    "DUC": "DUC",
}


def _status_from_curated(raw: str | None) -> str:
    """Map curated.wells.well_status → app's WellStatus enum value."""
    if raw is None:
        return "UNKNOWN"
    return _STATUS_REMAP.get(raw, "UNKNOWN")


# Column list pulled from curated.wells_enriched. Order matters only for
# readability; we read by alias name via mappings(). ST_AsText keeps the
# wellstick as a WKT string so the SQLAlchemy result row stays simple
# Python types — geometry parsing happens at persistence time.
_FETCH_ONE_SQL = text(
    """
    SELECT
        api10,
        api14_unformatted                 AS api14,
        well_name                         AS name,
        current_operator                  AS operator,
        formation,
        formation_blueox,
        basin_blueox,
        first_production_date             AS first_prod_date,
        lateral_length_ft                 AS lateral_ft,
        proppant_lbs,
        fluid_bbl,
        tvd_ft,
        county,
        basin,
        subbasin,
        well_status                       AS status_raw,
        surface_lat                       AS sh_lat,
        surface_lon                       AS sh_lon,
        bhl_lat                           AS bh_lat,
        bhl_lon                           AS bh_lon,
        ST_AsText(wellstick_geom)         AS wellstick_wkt,
        first_production_year             AS vintage_year,
        completion_vintage_bucket,
        is_horizontal,
        directional_survey_is_planned,
        eur_50yr_oil_bbl                  AS novi_oil_eur,
        lateral_closer_xy_ft
    FROM curated.wells_enriched
    WHERE api10 = :api10
    """
)


def _row_to_dto(row) -> WellHeader:  # type: ignore[no-untyped-def]
    """Build a WellHeader from a SQLAlchemy result mapping."""

    def _to_float(v: object) -> float | None:
        # curated stores some intensity columns as bigint/integer; widen
        # to float for downstream rate math.
        return float(v) if v is not None else None  # type: ignore[arg-type]

    return WellHeader(
        api10=row["api10"],
        api14=row["api14"],
        name=row["name"],
        operator=row["operator"],
        formation=row["formation"],
        formation_blueox=row["formation_blueox"],
        basin_blueox=row["basin_blueox"],
        first_prod_date=row["first_prod_date"],
        lateral_ft=_to_float(row["lateral_ft"]),
        proppant_lbs=_to_float(row["proppant_lbs"]),
        fluid_bbl=_to_float(row["fluid_bbl"]),
        tvd_ft=_to_float(row["tvd_ft"]),
        county=row["county"],
        basin=row["basin"],
        subbasin=row["subbasin"],
        status=_status_from_curated(row["status_raw"]),
        sh_lat=row["sh_lat"],
        sh_lon=row["sh_lon"],
        bh_lat=row["bh_lat"],
        bh_lon=row["bh_lon"],
        wellstick_wkt=row["wellstick_wkt"],
        vintage_year=row["vintage_year"],
        completion_vintage_bucket=row["completion_vintage_bucket"],
        is_horizontal=row["is_horizontal"],
        directional_survey_is_planned=row["directional_survey_is_planned"],
        novi_oil_eur=_to_float(row["novi_oil_eur"]),
        lateral_closer_xy_ft=_to_float(row["lateral_closer_xy_ft"]),
    )


def fetch_well_by_api10(session: Session, api10: str) -> WellHeader | None:
    """Fetch a single well header from the warehouse.

    Returns ``None`` if no row matches — either the api10 is unknown to
    Novi, or the well exists but isn't in ``curated.wells_enriched``
    (most likely because it's outside the Permian scope or soft-deleted).

    The caller is responsible for session lifecycle; this function does
    not commit or close.
    """
    row = session.execute(_FETCH_ONE_SQL, {"api10": api10}).mappings().first()
    if row is None:
        return None
    return _row_to_dto(row)


# Column block reused by the bulk fetcher. Kept as a module-level constant
# so the single-well and bulk paths can't drift from each other.
_HEADER_COLUMNS_SQL = """
    api10,
    api14_unformatted                 AS api14,
    well_name                         AS name,
    current_operator                  AS operator,
    formation,
    formation_blueox,
    basin_blueox,
    first_production_date             AS first_prod_date,
    lateral_length_ft                 AS lateral_ft,
    proppant_lbs,
    fluid_bbl,
    tvd_ft,
    county,
    basin,
    subbasin,
    well_status                       AS status_raw,
    surface_lat                       AS sh_lat,
    surface_lon                       AS sh_lon,
    bhl_lat                           AS bh_lat,
    bhl_lon                           AS bh_lon,
    ST_AsText(wellstick_geom)         AS wellstick_wkt,
    first_production_year             AS vintage_year,
    completion_vintage_bucket,
    is_horizontal,
    directional_survey_is_planned,
    eur_50yr_oil_bbl                  AS novi_oil_eur,
    lateral_closer_xy_ft
"""


def fetch_well_headers(
    session: Session,
    *,
    first_completion_after: date | None = date(2010, 1, 1),
    horizontal_only: bool = True,
) -> Iterator[WellHeader]:
    """Stream well headers matching the scope filters from
    ``curated.wells_enriched``.

    Defaults match the type-curve app's canonical scope: first completion
    2010-01-01 onward, horizontal only. Both are overridable for testing
    or future scope changes; passing ``first_completion_after=None`` or
    ``horizontal_only=False`` widens the result.

    The completion-date floor admits one extra class: horizontals that
    carry NO completion date at all (``first_completion_date IS NULL`` —
    P&A / Abandoned / Spud / DUC / etc.). These are real drilled wellbores
    that narvi already shows, so anduin surfaces them too. Permits
    (``well_status`` ``Permit Approved`` / ``Permit Cancelled``) are the
    one exclusion — they are phantom locations with no physical wellbore.
    NULL-completion wells map to non-PDP statuses, so they never appear on
    the default PDP map view; they show only when their status facet is on.

    Permian scope is enforced upstream at the engineering_db layer
    (raw_enverus is filtered ``envregion='PERMIAN'`` at ingest, Novi's
    ``us-horizontals`` export is effectively Permian-only). No
    ``env_region`` filter here — it would otherwise exclude the ~330
    Novi-only wells that lack an Enverus match, which are still real
    Permian wells.

    Caller is responsible for session lifecycle.
    """
    where_clauses: list[str] = ["TRUE"]
    params: dict[str, object] = {}
    if first_completion_after is not None:
        # Vintage floor for wells that HAVE a completion date, OR a real
        # drilled wellbore with no completion date at all — but never a
        # permit (phantom location, no wellbore). See the docstring.
        where_clauses.append(
            "(first_completion_date >= :first_completion_after"
            " OR (first_completion_date IS NULL"
            " AND COALESCE(well_status, '') NOT ILIKE 'Permit%'))"
        )
        params["first_completion_after"] = first_completion_after
    if horizontal_only:
        where_clauses.append("is_horizontal = TRUE")

    sql = text(
        f"""
        SELECT
{_HEADER_COLUMNS_SQL}
        FROM curated.wells_enriched
        WHERE {" AND ".join(where_clauses)}
        ORDER BY api10
        """
    )
    result = session.execute(sql, params).mappings()
    for row in result:
        yield _row_to_dto(row)
