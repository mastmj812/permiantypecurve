"""Guard the MVT tile SQL against the ST_Transform-defeats-GiST regression.

The geom columns (sh_geom / bh_geom / wellstick) are stored in EPSG:4326
with per-column GiST indexes. The spatial filter must compare the BARE
4326 columns against the tile envelope transformed INTO 4326. Wrapping a
column in ST_Transform(col, 3857) inside the WHERE predicate hides it
from the planner and forces a full seq scan of `wells` on every tile —
the exact defect this test locks out. The ST_AsMVTGeom projection in the
SELECT legitimately transforms to 3857 (tile coordinate space); that is
NOT the filter and is expected to remain.
"""

from __future__ import annotations

from datetime import date

from app.db.models import WellStatus
from app.wells_api.filters import FilterSpec
from app.wells_api.tiles import _build_filter_sql, _lines_sql, _points_sql


def _compact(sql: str) -> str:
    """Whitespace-insensitive form so the assertions don't depend on how
    the SQL happens to be indented."""
    return "".join(sql.split())


def test_no_column_transform_inside_spatial_predicate() -> None:
    # The anti-pattern is ST_Intersects(ST_Transform(<column>, 3857), env)
    # — a transformed column as a predicate operand. It must appear in
    # NEITHER layer's SQL. (ST_AsMVTGeom(ST_Transform(...)) in the SELECT
    # is fine and is a different token.)
    for sql in (_points_sql(), _lines_sql()):
        assert "ST_Intersects(ST_Transform(" not in _compact(sql)


def test_envelope_is_transformed_to_column_srid() -> None:
    # Both layers must materialize the tile envelope in 4326 (the column
    # SRID) so the bare-column predicate is index-usable.
    for sql in (_points_sql(), _lines_sql()):
        assert "env_4326" in sql
        assert "ST_Transform(ST_TileEnvelope(:z,:x,:y),4326)" in _compact(sql)


def test_lines_predicate_uses_bare_indexed_column() -> None:
    # z >= 9 lines path: ST_Intersects on the bare wellstick column drives
    # ix_wells_wellstick_gist. This is the perf-critical high-zoom query.
    compact = _compact(_lines_sql())
    assert "ST_Intersects(w.wellstick,(SELECTenv_4326FROMbounds))" in compact


def test_points_predicate_uses_bare_indexed_columns() -> None:
    # z < 9 points path: per-column && (bbox overlap — the GiST-backed
    # operator) OR'd so the planner can BitmapOr the three geom indexes.
    compact = _compact(_points_sql())
    assert "w.sh_geom&&(SELECTenv_4326FROMbounds)" in compact
    assert "w.wellstick&&(SELECTenv_4326FROMbounds)" in compact
    assert "w.bh_geom&&(SELECTenv_4326FROMbounds)" in compact


def test_asmvtgeom_still_projects_to_tile_space() -> None:
    # The output projection to 3857 (tile coordinates) must be preserved —
    # we only changed the filter, not how geometry is encoded into the MVT.
    for sql in (_points_sql(), _lines_sql()):
        assert "ST_AsMVTGeom(" in sql
        assert "env_3857" in sql


# ---------------------------------------------------------------------
# Filter-mirror drift guard. The tile endpoint filters via raw SQL
# (_build_filter_sql) while select/summary filter via
# FilterSpec.to_sqlalchemy_clauses — two implementations of one spec.
# Both the spacing filter and the well-name filter shipped with the ORM
# side only, so the MAP silently ignored them while the lasso honored
# them (caught by the user on the well-name filter, 2026-08-07). This
# guard fails whenever a FilterSpec field produces an ORM clause but no
# raw-SQL fragment, so the third landing spot can't be missed again.
# ---------------------------------------------------------------------

# One activating sample per FilterSpec field. A new field added without
# a sample here fails the completeness check below — extend BOTH this
# map and (if it filters) _build_filter_sql.
_FIELD_SAMPLES: dict[str, object] = {
    "formations": ("WOLFCAMP A",),
    "operators": ("OP A",),
    "counties": ("LOVING",),
    "statuses": (WellStatus.SI,),
    "first_prod_start": date(2018, 1, 1),
    "first_prod_end": date(2025, 1, 1),
    "lateral_min_ft": 6000.0,
    "lateral_max_ft": 12000.0,
    "spacing_min_ft": 400.0,
    "spacing_max_ft": 1000.0,
    # Sampled as False: both default True, so only an EXCLUSION filters.
    "spacing_include_no_neighbor": False,
    "spacing_include_no_data": False,
    "well_name_contains": "UNIVERSITY",
    "api10s": ("4200000001",),
}


def test_field_samples_cover_every_filterspec_field() -> None:
    import dataclasses

    spec_fields = {f.name for f in dataclasses.fields(FilterSpec)}
    assert spec_fields == set(_FIELD_SAMPLES), (
        "FilterSpec fields changed — add a sample (or an explicit None "
        "for inert-alone flags) AND, if the field filters, a raw-SQL "
        "fragment in tiles._build_filter_sql"
    )


def test_every_orm_clause_has_a_tile_sql_mirror() -> None:
    baseline_orm = len(FilterSpec().to_sqlalchemy_clauses())
    baseline = _build_filter_sql(FilterSpec())
    for field, sample in _FIELD_SAMPLES.items():
        if sample is None:
            continue
        spec = FilterSpec(**{field: sample})  # type: ignore[arg-type]
        adds_orm = len(spec.to_sqlalchemy_clauses()) > baseline_orm or (
            field == "statuses"  # replaces the default clause, count-neutral
        )
        if not adds_orm:
            continue
        # Compare (sql, params) — statuses keeps the same SQL string and
        # only changes the bind values.
        assert _build_filter_sql(spec) != baseline, (
            f"FilterSpec.{field} filters in to_sqlalchemy_clauses but "
            "produces NO fragment in tiles._build_filter_sql — the map "
            "would silently ignore it"
        )


def test_spacing_tile_sql_mirrors_orm_semantics() -> None:
    # Range alone: measured gated, both no-spacing classes ride along.
    sql, params = _build_filter_sql(FilterSpec(spacing_min_ft=1500.0))
    assert "lateral_closer_xy_ft IS NOT NULL" in sql
    assert "!= :spacing_sentinel" in sql
    assert params["spacing_sentinel"] == 2800.0
    assert "IS NULL" in sql and "= :spacing_sentinel" in sql

    # Both classes excluded: measured gate only, nothing OR'd back.
    sql_x, _ = _build_filter_sql(
        FilterSpec(
            spacing_min_ft=1500.0,
            spacing_include_no_neighbor=False,
            spacing_include_no_data=False,
        )
    )
    assert " OR " not in sql_x

    # Class flag live with NO range — the tile SQL must cull too, or the
    # map keeps showing wells the selection endpoint drops.
    sql_nr, params_nr = _build_filter_sql(FilterSpec(spacing_include_no_neighbor=False))
    assert "!= :spacing_sentinel" in sql_nr
    assert params_nr["spacing_sentinel"] == 2800.0
    assert "spacing_min_ft" not in params_nr

    # Fully unconstrained emits NO spacing fragment (only the default
    # status clause), so the unfiltered tile URL and ETag are unchanged.
    sql_base, params_base = _build_filter_sql(FilterSpec())
    assert "lateral_closer_xy_ft" not in sql_base
    assert "spacing_sentinel" not in params_base


def test_well_name_tile_sql_escapes_metacharacters() -> None:
    sql, params = _build_filter_sql(FilterSpec(well_name_contains="A_1 100%"))
    assert "w.name ILIKE :well_name_pat" in sql
    assert params["well_name_pat"] == r"%A\_1 100\%%"
