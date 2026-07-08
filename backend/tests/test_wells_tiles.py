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

from app.wells_api.tiles import _lines_sql, _points_sql


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
