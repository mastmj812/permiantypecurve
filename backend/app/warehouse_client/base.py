"""DTOs for the warehouse_client data layer.

These mirror ``enverus_client.base`` but are keyed on ``api10`` (Novi
wellbore identifier) rather than ``api14`` — the cutover from Enverus
direct ingest to engineering_db reads aligns the app with Novi's
wellbore-level data model. ``api14`` survives as a nullable secondary
column for cross-reference back to Enverus history.

These DTOs intentionally carry no ``raw`` payload field. Forensics moves
to querying ``engineering_db.raw_novi.*`` / ``raw_enverus.*`` directly;
keeping a JSONB blob on every well would duplicate that data.

See the ``project_permian_type_curve_cutover`` memory for the column
mapping (app field → curated source) and the locked design decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class WellHeader:
    """One row from ``curated.wells_enriched``, normalized for app ingest.

    ``status`` is already remapped from Novi's vocabulary (Active,
    Completed, Inactive, P&A, Abandoned, Spud, DUC) to the app's enum
    (PDP, INACTIVE, PA, DUC, UNKNOWN) by ``_status_from_curated``.

    ``wellstick_wkt`` is the lateral-path LINESTRING serialized as WKT
    in EPSG:4326: the Enverus survey-derived path from
    curated.enverus_lateral_lines when present (~99% of horizontals;
    traces u-turn/horseshoe geometry), else the 4-point Novi
    SHL→LP→MP→BHL curated.wells.wellstick_geom. The persistence layer
    parses it into a PostGIS geometry.

    ``directional_survey_is_planned`` is a formation-trust diagnostic:
    when TRUE, the well's formation assignment was made against the
    operator's pre-drill PLAN, not the actual post-drill survey, and
    should be treated as provisional. NM wells carry this flag at ~46%
    vs ~0.1% for TX. See the
    ``reference_directional_survey_trust`` memory.
    """

    api10: str
    api14: str | None = None
    name: str | None = None
    operator: str | None = None
    formation: str | None = None
    # Blue Ox standardized formation code + its basin
    # (curated.wells_enriched.formation_blueox / basin_blueox). Parallel to
    # raw ``formation``; the app's basin-aware formation coloring/filtering
    # keys on these. ``formation_blueox`` is NULL for unmapped wells;
    # ``basin_blueox`` is 'delaware'/'midland' (NULL outside those basins).
    formation_blueox: str | None = None
    basin_blueox: str | None = None
    first_prod_date: date | None = None
    lateral_ft: float | None = None
    proppant_lbs: float | None = None
    fluid_bbl: float | None = None
    tvd_ft: float | None = None
    county: str | None = None
    basin: str | None = None
    # Permian sub-basin (Delaware / Midland / …) — drives the basin-aware
    # terminal-Df in forecasting. From curated.wells_enriched.subbasin.
    subbasin: str | None = None
    status: str = "UNKNOWN"
    sh_lat: float | None = None
    sh_lon: float | None = None
    bh_lat: float | None = None
    bh_lon: float | None = None
    wellstick_wkt: str | None = None
    # Derived in curated.wells_enriched; included here so the app
    # doesn't need to recompute on persist.
    vintage_year: int | None = None
    completion_vintage_bucket: str | None = None
    is_horizontal: bool | None = None
    # Formation-trust flag. See class docstring.
    directional_survey_is_planned: bool | None = None
    # Novi's 50-yr oil EUR (curated.wells_enriched.eur_50yr_oil_bbl).
    # Pure passthrough; the Review table surfaces it as a benchmark
    # column. None when Novi hasn't forecasted the well.
    novi_oil_eur: float | None = None
    # Novi WellSpacing same-zone lateral offset (ft, XY plane), as-of-
    # first-production (curated.wells_enriched.lateral_closer_xy_ft).
    # None = absent from WellSpacing; exactly 2800.0 = Novi's
    # no-neighbor sentinel (passed through verbatim — the app's filter
    # layer owns the sentinel semantics).
    lateral_closer_xy_ft: float | None = None
    # Water-stream provenance flag (curated.water_data_quality.
    # water_source): 'measured' | 'calculated' | 'indeterminate' |
    # 'insufficient'. NULL = well absent from the matview (no producing
    # months). 'calculated' means the public water series is a vendor
    # formula (static WOR x oil), not measurement — FLAG ONLY by
    # convention of record (2026-08-17); nothing auto-excludes on it.
    water_source: str | None = None
    # Monthly-WOR coefficient of variation over the well's history
    # (curated.water_data_quality.wor_cv). Near-zero = dead-flat WOR,
    # the calculated signature. Diagnostic display only.
    wor_cv: float | None = None


@dataclass(frozen=True)
class ProductionRecord:
    """One row from ``curated.production``, api10-keyed.

    All three calendar-day rate columns (``rate_calday_*``) are direct
    passthroughs of ``curated.production.{oil,gas,water}_per_day_bbl/mcf``.
    Novi computes these calendar-day rates upstream — verified during the
    cutover audit (``oil_per_day_bbl * cal_days_in_month`` matches
    ``oil_per_month_bbl`` exactly, even on 23-day partial months). So
    the legacy app-side month-1 ``producing_days`` exception in
    ``ingest/rates.py`` is retired by the cutover; we just persist the
    values Novi already computed.
    """

    api10: str
    prod_date: date
    oil_bbl: float | None = None
    gas_mcf: float | None = None
    water_bbl: float | None = None
    # Float because Novi reports fractional producing-days for partial
    # first months (e.g. 0.166 = ~4 hours online). Integer truncation
    # would zero-out IP-rich first-month rates.
    producing_days: float | None = None
    # Canonical calendar-day rates. Direct passthrough from curated; no
    # app-side rate math needed.
    rate_calday_bopd: float | None = None
    rate_calday_mcfd: float | None = None
    rate_calday_bwpd: float | None = None


@dataclass(frozen=True)
class NoviForecastRecord:
    """One row from ``curated.production_forecast``, api10-keyed.

    Novi's forecasted (PDP) monthly series. Mirrors ``ProductionRecord``'s
    ``rate_calday_*`` naming so the local table and the curves overlay can
    treat forecast and actual identically. ``cumulative_*`` are Novi's
    running totals, carried for the optional cumulative overlay.
    """

    api10: str
    prod_date: date
    rate_calday_bopd: float | None = None
    rate_calday_mcfd: float | None = None
    rate_calday_bwpd: float | None = None
    cumulative_oil_bbl: float | None = None
    cumulative_gas_mcf: float | None = None
    cumulative_water_bbl: float | None = None
