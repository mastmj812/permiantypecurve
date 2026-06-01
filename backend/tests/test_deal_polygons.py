"""Deal-polygon helper tests.

The DB-integrated endpoints (upload / list / patch / delete) live
behind PostGIS, so we cover the pure-function pieces here and rely
on the manual verification path in the plan file for the round-trip
through Postgres.

What we pin:

  * ``_shape_to_wkt`` — pyshp shape → MULTIPOLYGON WKT shape
  * ``_pick_label``    — first non-numeric attribute wins; fallback to
                          "Polygon {fid}"
  * ``_resolve_source_epsg`` — recognises a standard .prj WKT
  * ``_color_for_deal`` — deterministic palette pick keyed by hash
  * ``_parse_shapefile_zip`` — round-trip: write a minimal shapefile to
                                a zip in memory, parse it back, verify
                                the (name, wkt, attributes) shape
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest
import shapefile  # pyshp

from app.api.deal_polygons import (
    _color_for_deal,
    _parse_shapefile_zip,
    _pick_label,
    _resolve_source_epsg,
    _shape_to_wkt,
)


def test_pick_label_prefers_first_non_numeric() -> None:
    attrs = {"FID": 0, "ACRES": 1320.5, "NAME": "North Tract", "OWNER": "Op A"}
    assert _pick_label(attrs, fid=7) == "North Tract"


def test_pick_label_falls_back_when_all_numeric() -> None:
    attrs = {"FID": 0, "ACRES": 1320.5, "OBJECTID": 12}
    assert _pick_label(attrs, fid=7) == "Polygon 7"


def test_pick_label_falls_back_when_empty() -> None:
    assert _pick_label({}, fid=3) == "Polygon 3"


def test_color_for_deal_is_deterministic() -> None:
    deal = uuid.uuid4()
    assert _color_for_deal(deal) == _color_for_deal(deal)


def test_color_for_deal_unlinked_neutral() -> None:
    assert _color_for_deal(None) == "#9ca3af"


def test_resolve_source_epsg_wgs84() -> None:
    # Standard WGS84 .prj WKT — pyproj should map it to 4326.
    wgs84_wkt = (
        'GEOGCS["GCS_WGS_1984",'
        'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
    )
    assert _resolve_source_epsg(wgs84_wkt) == 4326


def test_resolve_source_epsg_missing_prj_returns_none() -> None:
    assert _resolve_source_epsg(None) is None
    assert _resolve_source_epsg("") is None


def test_shape_to_wkt_simple_square() -> None:
    class _MockShape:
        # pyshp Shape duck — only the attrs _shape_to_wkt reads.
        parts = [0]
        points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]

    wkt = _shape_to_wkt(_MockShape())  # type: ignore[arg-type]
    assert wkt is not None
    assert wkt.startswith("MULTIPOLYGON ((")
    # The four corners + closing repeat should all appear in order.
    for token in ("0.0 0.0", "1.0 0.0", "1.0 1.0", "0.0 1.0"):
        assert token in wkt


def test_shape_to_wkt_closes_open_ring() -> None:
    class _MockShape:
        parts = [0]
        points = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]  # not closed

    wkt = _shape_to_wkt(_MockShape())  # type: ignore[arg-type]
    assert wkt is not None
    # The helper closes the ring → the trailing point repeats the first.
    assert wkt.endswith("0.0 0.0))")


def test_parse_shapefile_zip_roundtrip() -> None:
    # Build a minimal in-memory shapefile (.shp + .dbf + .prj), zip it,
    # parse it back, and confirm we recover one feature with the
    # expected name + wkt + attributes.
    shp_buf = io.BytesIO()
    shx_buf = io.BytesIO()
    dbf_buf = io.BytesIO()
    with shapefile.Writer(shp=shp_buf, shx=shx_buf, dbf=dbf_buf) as w:
        w.field("NAME", "C", size=40)
        w.field("ACRES", "N", decimal=1)
        w.poly([[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]])
        w.record(NAME="South Tract", ACRES=640.0)

    prj_wkt = (
        'GEOGCS["GCS_WGS_1984",'
        'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
    )

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("tracts.shp", shp_buf.getvalue())
        zf.writestr("tracts.shx", shx_buf.getvalue())
        zf.writestr("tracts.dbf", dbf_buf.getvalue())
        zf.writestr("tracts.prj", prj_wkt)

    features, source_epsg = _parse_shapefile_zip(zip_buf.getvalue())
    assert len(features) == 1
    name, wkt, attrs = features[0]
    assert name == "South Tract"
    assert wkt.startswith("MULTIPOLYGON ((")
    assert attrs == {"NAME": "South Tract", "ACRES": 640.0}
    assert source_epsg == 4326


def test_parse_shapefile_zip_rejects_missing_dbf() -> None:
    # zip with only a .shp → should raise (400). Build a tiny shp
    # blob with pyshp first so we have something to zip.
    shp_buf = io.BytesIO()
    shx_buf = io.BytesIO()
    dbf_buf = io.BytesIO()
    with shapefile.Writer(shp=shp_buf, shx=shx_buf, dbf=dbf_buf) as w:
        w.field("NAME", "C", size=10)
        w.poly([[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]])
        w.record(NAME="x")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("only.shp", shp_buf.getvalue())  # no .dbf — invalid

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _parse_shapefile_zip(zip_buf.getvalue())
    assert exc.value.status_code == 400
