"""GeoPackage upload parsing — pure-function tests (same pattern as
test_deal_polygons.py: the DB round-trip stays manually verified; here we pin
the reader and ``_parse_gpkg``).

What we pin:

  * ``sniff_format``   — magic-byte dispatch (zip / gpkg / unknown)
  * header flags       — envelope sizes 0/1, empty-flag skip, extended-GPB skip
  * ``_parse_gpkg``    — (name, wkt, attributes, src_epsg) shape; DSU_Num
                          labels; verbatim attributes + injected gpkg_layer;
                          interior rings PRESERVED in the WKT (the legacy
                          ``_shape_to_wkt`` shapefile path drops them);
                          Z flattened; declared-vs-stored geometry-type
                          mismatch tolerated in both directions
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Any

import pytest
import shapely.wkb
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Polygon

from app.api.deal_polygons import _parse_gpkg
from app.gpkg_reader import read_gpkg, sniff_format

_D = 0.017  # ~1 sq mile in degrees near the Delaware Basin AOI


def _square(lon: float, lat: float) -> list[tuple[float, float]]:
    return [(lon, lat), (lon + _D, lat), (lon + _D, lat + _D), (lon, lat + _D), (lon, lat)]


def _sq(lon: float, lat: float, hole: bool = False) -> Polygon:
    holes = None
    if hole:
        m = _D / 4
        holes = [
            [
                (lon + m, lat + m),
                (lon + 2 * m, lat + m),
                (lon + 2 * m, lat + 2 * m),
                (lon + m, lat + 2 * m),
                (lon + m, lat + m),
            ]
        ]
    return Polygon(_square(lon, lat), holes)


def _gpkg_blob(
    geom: Polygon | MultiPolygon,
    *,
    empty: bool = False,
    extended: bool = False,
    envelope: bool = False,
) -> bytes:
    """GPKG binary geometry: magic 'GP', version 0, flags (little-endian byte
    order + envelope indicator + optional empty/extended bits), srs_id int32,
    optional 32-byte XY envelope, then plain WKB."""
    flags = 0x01 | (0x02 if envelope else 0) | (0x10 if empty else 0) | (0x20 if extended else 0)
    header = b"GP\x00" + bytes([flags]) + struct.pack("<i", 4326)
    if envelope:
        minx, miny, maxx, maxy = geom.bounds
        header += struct.pack("<4d", minx, maxx, miny, maxy)
    return header + shapely.wkb.dumps(geom)


def _make_gpkg(
    tmp_path: Path,
    rows: list[tuple[dict[str, Any], bytes]],
    layer: str = "Toucan v2",
    declared: str = "MULTIPOLYGON",
) -> bytes:
    path = tmp_path / "fixture.gpkg"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL,
            srs_id INTEGER PRIMARY KEY, organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL,
            description TEXT);
        INSERT INTO gpkg_spatial_ref_sys VALUES
            ('WGS 84', 4326, 'EPSG', 4326, 'unused-by-reader', NULL);
        CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY,
            data_type TEXT NOT NULL, identifier TEXT, description TEXT,
            last_change DATETIME, min_x DOUBLE, min_y DOUBLE, max_x DOUBLE,
            max_y DOUBLE, srs_id INTEGER);
        CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL,
            column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL);
        """
    )
    cols: list[str] = []
    for attrs, _ in rows:
        for k in attrs:
            if k not in cols:
                cols.append(k)
    q = layer.replace('"', '""')
    col_ddl = "".join(f', "{c}" TEXT' for c in cols)
    conn.execute(f'CREATE TABLE "{q}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB{col_ddl})')
    conn.execute(
        "INSERT INTO gpkg_contents (table_name, data_type, identifier, srs_id)"
        " VALUES (?, 'features', ?, 4326)",
        (layer, layer),
    )
    conn.execute(
        "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, 4326, 0, 0)",
        (layer, declared),
    )
    col_names = "".join(f', "{c}"' for c in cols)
    ph = ", ".join(["?"] * (1 + len(cols)))
    for attrs, blob in rows:
        conn.execute(
            f'INSERT INTO "{q}" (geom{col_names}) VALUES ({ph})',
            [blob] + [attrs.get(c) for c in cols],
        )
    conn.commit()
    conn.close()
    return path.read_bytes()


def test_sniff_format() -> None:
    assert sniff_format(b"PK\x03\x04rest") == "zip"
    assert sniff_format(b"SQLite format 3\x00rest") == "gpkg"
    assert sniff_format(b"GIF89a") == "unknown"
    assert sniff_format(b"") == "unknown"


def test_header_flags_envelope_empty_extended(tmp_path: Path) -> None:
    rows = [
        ({}, _gpkg_blob(_sq(-103.8, 31.9))),  # no envelope
        ({}, _gpkg_blob(_sq(-103.7, 31.9), envelope=True)),  # 32-byte XY envelope
        ({}, _gpkg_blob(_sq(-103.6, 31.9), empty=True)),  # skipped
        ({}, _gpkg_blob(_sq(-103.5, 31.9), extended=True)),  # skipped
    ]
    layers = read_gpkg(_make_gpkg(tmp_path, rows))
    assert len(layers) == 1
    assert len(layers[0].features) == 2  # empty + extended rows dropped


def test_parse_gpkg_toucan_v2_shape(tmp_path: Path) -> None:
    rows = [
        (
            {"Type": "Tract", "Section": "33", "Min_Depth": "Surface", "Max_Depth": "9,515"},
            _gpkg_blob(_sq(-103.8, 31.9)),
        ),
        (
            {"Type": "DSU", "DSU_Num": "33", "Max_Depth": "9,515'", "DSU_WI": "1.0"},
            _gpkg_blob(_sq(-103.8, 31.9)),
        ),
    ]
    features = _parse_gpkg(_make_gpkg(tmp_path, rows))
    # anduin ingests ALL rows (tracts included — JSONB storage wants them)
    assert len(features) == 2
    tract, dsu = features
    # tract has no whitelisted name column -> layer+fid fallback
    assert tract[0] == "Toucan v2 1"
    assert dsu[0] == "DSU 33"
    assert tract[2]["Max_Depth"] == "9,515"  # raw text, uninterpreted
    assert tract[2]["gpkg_layer"] == "Toucan v2"
    assert dsu[2]["DSU_WI"] == "1.0"
    assert all(f[3] == 4326 for f in features)
    assert all(f[1].startswith("POLYGON") for f in features)


def test_parse_gpkg_preserves_interior_rings(tmp_path: Path) -> None:
    data = _make_gpkg(tmp_path, [({}, _gpkg_blob(_sq(-103.8, 31.9, hole=True)))])
    (feature,) = _parse_gpkg(data)
    geom = shapely_wkt.loads(feature[1])
    assert isinstance(geom, Polygon)
    assert len(geom.interiors) == 1  # the legacy shapefile path drops these


def test_parse_gpkg_flattens_z(tmp_path: Path) -> None:
    zpoly = Polygon([(x, y, 100.0) for x, y in _square(-103.8, 31.9)])
    (feature,) = _parse_gpkg(_make_gpkg(tmp_path, [({}, _gpkg_blob(zpoly))]))
    assert not shapely_wkt.loads(feature[1]).has_z


def test_declared_vs_stored_type_mismatch_both_directions(tmp_path: Path) -> None:
    # declared MULTIPOLYGON, stored POLYGON
    a = _make_gpkg(
        tmp_path, [({}, _gpkg_blob(_sq(-103.8, 31.9)))], layer="a", declared="MULTIPOLYGON"
    )
    assert len(read_gpkg(a)[0].features) == 1
    # declared POLYGON, stored MULTIPOLYGON
    mp = MultiPolygon([_sq(-103.8, 31.9), _sq(-103.7, 31.9)])
    b = _make_gpkg(tmp_path, [({}, _gpkg_blob(mp))], layer="b", declared="POLYGON")
    assert read_gpkg(b)[0].features[0].geometry.geom_type == "MultiPolygon"


def test_not_a_gpkg_raises() -> None:
    with pytest.raises(ValueError, match="Not a GeoPackage"):
        read_gpkg(b"GIF89a not a database")
