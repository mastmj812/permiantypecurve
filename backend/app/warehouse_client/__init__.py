"""Read-from-warehouse data layer.

Replaces the legacy ``enverus_client`` HTTP-API ingest. Reads from
``engineering_db``'s ``curated.*`` materialized views (wells, production,
type_curve_cohorts) and emits api10-keyed DTOs.

See the ``project_permian_type_curve_cutover`` memory for the cutover
plan, the column mapping from app fields → curated columns, and the
locked decisions (api14→api10 PK migration, ``raw_payload`` drop, status
enum remap, calendar-day rate semantics).
"""
