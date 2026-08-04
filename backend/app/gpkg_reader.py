"""GeoPackage (.gpkg) reader — stdlib sqlite3 + shapely WKB decode; no GDAL.

A GeoPackage is a SQLite database (OGC spec): feature tables are enumerated in
gpkg_contents / gpkg_geometry_columns, and each geometry cell is a GPKG binary
blob (fixed header + WKB). This reader covers exactly what deal deliverables
need — polygon feature layers with attributes — and never touches tile/raster
tables.

Land-department convention (schema of record, Toucan v2 2026-08): ONE layer per
deal (layer name = deal name) with a `Type` TEXT column discriminating 'DSU' vs
'Tract' rows; depth/WI columns ride along as attributes. Early deliveries
(Toucan v1) are geometry-only — no Type column, no attributes — so everything
here is tolerant: attributes are optional, declared geometry types are never
trusted (files lie), and non-polygonal rows are skipped, not fatal.

Copy of the canonical module in narvi (src/narvi/gpkg_reader.py); the repos are
independent — same copied-module pattern as the pyshp shapefile readers.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Any, Literal

from pyproj import CRS
from shapely import force_2d
from shapely import wkb as shapely_wkb
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# Deal feature layers are tiny; anything bigger is a wrong file (rasters, a
# whole county) and would only waste the temp-file write.
MAX_GPKG_BYTES = 50 * 1024 * 1024

_SQLITE_MAGIC = b"SQLite format 3\x00"
# GPKG header flags bits 1-3 (envelope indicator) -> envelope byte length.
# 5-7 are invalid per spec.
_ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def sniff_format(data: bytes) -> Literal["zip", "gpkg", "unknown"]:
    """Dispatch on file magic, not extension: 'PK' = zipped shapefile,
    SQLite header = GeoPackage."""
    if data[:2] == b"PK":
        return "zip"
    if data[:16] == _SQLITE_MAGIC:
        return "gpkg"
    return "unknown"


@dataclass
class GpkgFeature:
    fid: int
    geometry: Polygon | MultiPolygon  # source CRS, force-2D, holes kept
    attributes: dict[str, Any]  # JSON-safe; excludes pk + geometry cols


@dataclass
class GpkgLayer:
    table_name: str
    srs_id: int
    crs: CRS
    features: list[GpkgFeature]  # polygonal, non-empty rows only


def read_gpkg(data: bytes) -> list[GpkgLayer]:
    """GeoPackage bytes -> polygon-bearing feature layers (gpkg_contents order).
    Raises ValueError when the file is not a gpkg or holds no polygon features."""
    if len(data) > MAX_GPKG_BYTES:
        raise ValueError(f"GeoPackage exceeds {MAX_GPKG_BYTES // (1024 * 1024)} MB.")
    if sniff_format(data) != "gpkg":
        raise ValueError("Not a GeoPackage (SQLite) file.")
    # sqlite3 can't open from bytes: temp file, opened read-only + immutable so
    # no journal/WAL is created. The handle must be closed before connecting,
    # and the connection closed before unlink (Windows share locks).
    fd, path = tempfile.mkstemp(suffix=".gpkg")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        uri = "file:" + path.replace(os.sep, "/") + "?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        try:
            return _read_layers(conn)
        finally:
            conn.close()
    finally:
        # transient Windows handle — a leaked temp file must not fail the upload
        with contextlib.suppress(OSError):
            os.unlink(path)


def split_roles(
    features: list[GpkgFeature],
) -> tuple[list[GpkgFeature], list[GpkgFeature], list[GpkgFeature]]:
    """Partition by the land-department `Type` column -> (dsus, tracts, generic).
    Case/whitespace-insensitive; unknown or absent Type -> generic (mapped like
    a unit shape, never dropped)."""
    dsus: list[GpkgFeature] = []
    tracts: list[GpkgFeature] = []
    generic: list[GpkgFeature] = []
    for f in features:
        t = ""
        for k, v in f.attributes.items():
            if k.strip().casefold() == "type" and isinstance(v, str):
                t = v.strip().casefold()
                break
        (dsus if t == "dsu" else tracts if t == "tract" else generic).append(f)
    return dsus, tracts, generic


# Label preference order. DSU_Num leads (the land convention names units by
# number: "31", "12-15"); the rest mirrors the name-field whitelist the apps
# already use for shapefile DBF labels.
_LABEL_FIELDS = (
    "dsu_num",
    "dsu_name",
    "unit_name",
    "unit",
    "deal_name",
    "dealname",
    "deal",
    "name",
    "prospect",
    "label",
    "title",
    "tract_id",
    "deal_id",
    "id",
)


def pick_label(attrs: dict[str, Any], fallback: str) -> str:
    """First non-empty whitelisted name attribute, else the fallback.
    DSU_Num is prefixed ('31' -> 'DSU 31') so bare numbers stay readable."""
    low = {k.strip().casefold(): v for k, v in attrs.items()}
    for key in _LABEL_FIELDS:
        v = low.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return f"DSU {s}" if key == "dsu_num" else s
    return fallback


# ---------------------------------------------------------------------------
# internals


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _read_layers(conn: sqlite3.Connection) -> list[GpkgLayer]:
    try:
        rows = conn.execute(
            "SELECT c.table_name, g.column_name, g.srs_id "
            "FROM gpkg_contents c "
            "JOIN gpkg_geometry_columns g ON g.table_name = c.table_name "
            "WHERE c.data_type = 'features'"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f"Not a valid GeoPackage: {exc}") from exc
    layers: list[GpkgLayer] = []
    for table, geom_col, srs_id in rows:
        feats = _read_features(conn, table, geom_col)
        if feats:
            layers.append(
                GpkgLayer(
                    table_name=table,
                    srs_id=srs_id,
                    crs=_resolve_crs(conn, srs_id),
                    features=feats,
                )
            )
    if not layers:
        raise ValueError("GeoPackage contains no polygon features.")
    return layers


def _read_features(conn: sqlite3.Connection, table: str, geom_col: str) -> list[GpkgFeature]:
    try:
        info = conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    except sqlite3.Error:
        return []
    cols = [r[1] for r in info]
    if geom_col not in cols:
        return []
    pks = [r[1] for r in info if r[5]]
    pk = pks[0] if len(pks) == 1 else None
    attr_cols = [c for c in cols if c not in (geom_col, pk)]
    sel = ", ".join(_quote_ident(c) for c in ([pk] if pk else []) + [geom_col] + attr_cols)
    feats: list[GpkgFeature] = []
    for i, row in enumerate(conn.execute(f"SELECT {sel} FROM {_quote_ident(table)}")):
        off = 1 if pk else 0
        blob = row[off]
        geom = _geom_from_blob(blob) if isinstance(blob, bytes) else None
        if geom is None:
            continue
        fid = row[0] if pk and isinstance(row[0], int) else i
        attrs = {c: _jsonable(v) for c, v in zip(attr_cols, row[off + 1 :], strict=False)}
        feats.append(GpkgFeature(fid=fid, geometry=geom, attributes=attrs))
    return feats


def _geom_from_blob(blob: bytes) -> Polygon | MultiPolygon | None:
    """GPKG binary blob -> polygonal shapely geometry, or None to skip the row
    (empty flag, extended-GPB, invalid header, non-polygonal WKB)."""
    if len(blob) < 8 or blob[:2] != b"GP":
        return None
    flags = blob[3]
    if flags & 0x20:  # extended GeoPackage binary — unsupported
        return None
    if flags & 0x10:  # empty-geometry flag
        return None
    env = _ENVELOPE_BYTES.get((flags >> 1) & 0x07)
    if env is None:  # invalid envelope indicator (5-7)
        return None
    try:
        geom = shapely_wkb.loads(bytes(blob[8 + env :]))
    except Exception:  # malformed WKB is a skip, not a 500
        return None
    return _polygonal(force_2d(geom))


def _polygonal(geom: BaseGeometry) -> Polygon | MultiPolygon | None:
    """Keep Polygon/MultiPolygon; salvage polygonal parts of a
    GeometryCollection (odd writers emit them); drop everything else. The
    declared geometry_type_name is deliberately never consulted — classify per
    parsed blob."""
    if geom.is_empty:
        return None
    if isinstance(geom, Polygon | MultiPolygon):
        return geom
    if isinstance(geom, GeometryCollection):
        polys = [
            g for g in geom.geoms if isinstance(g, Polygon | MultiPolygon) and not g.is_empty
        ]
        if polys:
            u = unary_union(polys)
            if isinstance(u, Polygon | MultiPolygon):
                return u
    return None


def _resolve_crs(conn: sqlite3.Connection, srs_id: int) -> CRS:
    """srs_id 0/-1 (gpkg 'undefined') -> 4326; else EPSG; else the stored WKT
    definition; else 4326 — the same silent fallback as a shapefile with no
    .prj member."""
    if srs_id in (0, -1):
        return CRS.from_epsg(4326)
    with contextlib.suppress(Exception):
        return CRS.from_epsg(srs_id)
    with contextlib.suppress(Exception):
        row = conn.execute(
            "SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs_id,)
        ).fetchone()
        if row and row[0]:
            return CRS.from_wkt(row[0])
    return CRS.from_epsg(4326)


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | float | str):
        return v
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(v)
