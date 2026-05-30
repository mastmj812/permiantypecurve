"""Vector tile endpoint for the map page.

Tile geometry is rendered server-side by PostGIS:
  * `ST_TileEnvelope(z, x, y)` produces the tile bounds in EPSG:3857.
  * `ST_AsMVTGeom` clips and re-projects each well to tile coordinates.
  * `ST_AsMVT` packages the rows into a binary MVT payload.

Two source layers ship per tile, but only one is populated at any zoom:
  * `wells_points` (z < 9)  — surface-hole centroid as a point
  * `wells_lines`  (z ≥ 9)  — full wellstick LINESTRING

The frontend layer config matches min/maxzoom so the same source serves
both. Splitting by zoom keeps low-zoom tiles cheap (point per row) and
high-zoom tiles useful (full lateral sticks).

Tile cache: a short browser cache + an ETag tied to the filter spec hash
means panning re-uses tiles within a session, while filter changes
invalidate cleanly without server bookkeeping.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.session import get_session
from app.wells_api.filters import FilterSpec, filter_spec_dict, parse_filter_query

router = APIRouter(prefix="/wells", tags=["wells"])
log = get_logger("api.wells.tiles")

# Cutoff zoom for points vs. lines. The brief calls for clusters below 8
# and wellsticks at 9+; we keep the same cutoff but emit centroids instead
# of formal clusters for v1 (lighter and renders fine at z 3-8).
POINTS_MAX_Z = 8
LINES_MIN_Z = 9

MVT_CONTENT_TYPE = "application/vnd.mapbox-vector-tile"


def _filter_etag(spec: FilterSpec, z: int, x: int, y: int) -> str:
    payload = {"spec": filter_spec_dict(spec), "z": z, "x": x, "y": y}
    h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f'W/"{h[:16]}"'


def _build_filter_sql(spec: FilterSpec) -> tuple[str, dict[str, object]]:
    """Return a SQL fragment of AND-joined conditions and a bind param map.

    Mirrors `FilterSpec.to_sqlalchemy_clauses` but emits raw SQL because we
    embed it inside the larger ST_AsMVT statement (which is also raw)."""
    parts: list[str] = []
    params: dict[str, object] = {}

    # Explicit ::TEXT[] / ::well_status[] casts make the bind unambiguous
    # for psycopg — it adapts Python lists to Postgres arrays, but the
    # array element type needs to be pinned for the ANY() comparison.
    if spec.formations:
        parts.append("w.formation = ANY((:formations)::TEXT[])")
        params["formations"] = list(spec.formations)
    if spec.operators:
        parts.append("w.operator = ANY((:operators)::TEXT[])")
        params["operators"] = list(spec.operators)
    if spec.counties:
        parts.append("w.county = ANY((:counties)::TEXT[])")
        params["counties"] = list(spec.counties)
    if spec.statuses:
        parts.append("w.status = ANY((:statuses)::well_status[])")
        params["statuses"] = [s.value for s in spec.statuses]
    if spec.first_prod_start is not None:
        parts.append("w.first_prod_date >= :first_prod_start")
        params["first_prod_start"] = spec.first_prod_start
    if spec.first_prod_end is not None:
        parts.append("w.first_prod_date <= :first_prod_end")
        params["first_prod_end"] = spec.first_prod_end
    if spec.lateral_min_ft is not None:
        parts.append("w.lateral_ft >= :lateral_min_ft")
        params["lateral_min_ft"] = spec.lateral_min_ft
    if spec.lateral_max_ft is not None:
        parts.append("w.lateral_ft <= :lateral_max_ft")
        params["lateral_max_ft"] = spec.lateral_max_ft
    if spec.api10s:
        # Explicit allow-list pasted in the FilterPanel. Mirrors the
        # selection endpoint's behavior so the same paste produces the
        # same wells on the map and in /api/wells/select.
        parts.append("w.api10 = ANY((:api10s)::TEXT[])")
        params["api10s"] = list(spec.api10s)

    if not parts:
        return "TRUE", params
    return " AND ".join(parts), params


def _points_sql() -> str:
    """Tile content for z < 9: well surface points (or centroid fallback)."""
    return """
WITH bounds AS (
  SELECT ST_TileEnvelope(:z, :x, :y) AS env_3857
),
mvtgeom AS (
  SELECT
    w.api10,
    w.name,
    w.formation,
    w.operator,
    w.status::text AS status,
    w.lateral_ft,
    EXTRACT(YEAR FROM w.first_prod_date)::INT AS vintage_year,
    ST_AsMVTGeom(
      ST_Transform(
        COALESCE(w.sh_geom, ST_StartPoint(w.wellstick), w.bh_geom),
        3857
      ),
      (SELECT env_3857 FROM bounds),
      4096, 64, true
    ) AS geom
  FROM wells w
  WHERE COALESCE(w.sh_geom, w.wellstick, w.bh_geom) IS NOT NULL
    AND ST_Intersects(
      ST_Transform(COALESCE(w.sh_geom, ST_Centroid(w.wellstick), w.bh_geom), 3857),
      (SELECT env_3857 FROM bounds)
    )
    AND ({filter_sql})
)
SELECT ST_AsMVT(mvtgeom.*, 'wells_points', 4096, 'geom')
FROM mvtgeom
WHERE geom IS NOT NULL
"""


def _lines_sql() -> str:
    """Tile content for z ≥ 9: full wellstick LINESTRINGs."""
    return """
WITH bounds AS (
  SELECT ST_TileEnvelope(:z, :x, :y) AS env_3857
),
mvtgeom AS (
  SELECT
    w.api10,
    w.name,
    w.formation,
    w.operator,
    w.status::text AS status,
    w.lateral_ft,
    EXTRACT(YEAR FROM w.first_prod_date)::INT AS vintage_year,
    ST_AsMVTGeom(
      ST_Transform(w.wellstick, 3857),
      (SELECT env_3857 FROM bounds),
      4096, 64, true
    ) AS geom
  FROM wells w
  WHERE w.wellstick IS NOT NULL
    AND ST_Intersects(
      ST_Transform(w.wellstick, 3857),
      (SELECT env_3857 FROM bounds)
    )
    AND ({filter_sql})
)
SELECT ST_AsMVT(mvtgeom.*, 'wells_lines', 4096, 'geom')
FROM mvtgeom
WHERE geom IS NOT NULL
"""


@router.get(
    "/tiles/{z}/{x}/{y}.mvt",
    responses={
        200: {"content": {MVT_CONTENT_TYPE: {}}},
        204: {"description": "Tile contains no features (empty MVT)"},
    },
)
def well_tile(
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    spec: FilterSpec = Depends(parse_filter_query),
    always_lines: bool = Query(
        default=False,
        description=(
            "When true, return wellstick LINESTRINGs at every zoom instead "
            "of the surface-point fallback below z=9. Used by the PowerPoint "
            "slide map so wells render as sticks regardless of cohort spread."
        ),
    ),
    session: Session = Depends(get_session),
) -> Response:
    if x >= (1 << z) or y >= (1 << z):
        raise HTTPException(status_code=400, detail="tile coords out of range for z")

    filter_sql, params = _build_filter_sql(spec)
    params.update({"z": z, "x": x, "y": y})

    use_lines = always_lines or z > POINTS_MAX_Z
    body_template = _lines_sql() if use_lines else _points_sql()
    sql = body_template.format(filter_sql=filter_sql)

    result = session.execute(text(sql), params).scalar()
    payload: bytes = bytes(result) if result is not None else b""

    etag = _filter_etag(spec, z, x, y)
    if not payload:
        return Response(
            status_code=204,
            headers={"ETag": etag, "Cache-Control": "public, max-age=30"},
        )
    return Response(
        content=payload,
        media_type=MVT_CONTENT_TYPE,
        headers={
            "ETag": etag,
            # 30s lets a pan-and-back re-use tiles within a session, but
            # any filter change still flushes via the URL query string.
            "Cache-Control": "public, max-age=30",
        },
    )
