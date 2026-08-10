"""Build-up starting universe: formation wells inside the drawn AOI.

The "type well build-up" starts from the wells of the curve's
formation(s) inside the union of the drawn selection polygons — NO
vintage / lateral / status / operator filters, so those culls become
explicit, countable waterfall stages downstream (buildup.py).

Deliberately NOT the /api/wells/select path: that endpoint applies the
active FilterSpec and enforces the 500-well selection cap, both of which
would silently shrink the universe. This module shares its spatial
predicate (ST_Intersects against coalesce(wellstick, sh_geom)) but runs
uncapped, with a 10k sanity ceiling against pathological AOIs.

The result is snapshotted into ``type_curves.provenance`` at save time —
per-well attrs included — so the build-up sheet reports the universe as
it stood when the curve was built, unaffected by later warehouse syncs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Well

log = get_logger("type_curves.universe")

# Pathology guard, not a product limit: a basin-scale AOI is a mistake,
# not a cohort. Hit → truncate, warn, and flag in the snapshot.
UNIVERSE_SANITY_CAP = 10_000

_SQ_METERS_PER_SQ_MILE = 2_589_988.110336


def _geom_from_geojson(polygon: dict[str, Any], key: str) -> Any:
    # Same construction as wells_api/selection.py: GeoJSON dict → JSON
    # string → ST_GeomFromGeoJSON (which wants text, not a record).
    return text(f"ST_SetSRID(ST_GeomFromGeoJSON(:{key}), 4326)").bindparams(
        **{key: json.dumps(polygon)}
    )


def universe_stmt(polygons: list[dict[str, Any]], formations: list[str]) -> Select[Any]:
    """Uncapped select of formation wells intersecting ANY of the polygons.

    Pure statement builder — kept separate from execution so tests can
    assert the predicate shape (formation-only filter, OR'd polygons,
    no 500-well cap) without a live PostGIS.
    """
    candidate = func.coalesce(Well.wellstick, Well.sh_geom)
    spatial = or_(
        *[
            func.ST_Intersects(candidate, _geom_from_geojson(p, f"aoi_{i}"))
            for i, p in enumerate(polygons)
        ]
    )
    return (
        select(
            Well.api10,
            Well.name,
            Well.operator,
            Well.formation_blueox,
            Well.first_prod_date,
            Well.lateral_ft,
            Well.status,
            Well.lateral_closer_xy_ft,
        )
        .where(spatial, Well.formation_blueox.in_(formations))
        .order_by(Well.api10)
        .limit(UNIVERSE_SANITY_CAP + 1)
    )


def compute_universe(
    session: Session,
    polygons: list[dict[str, Any]],
    formations: list[str],
) -> dict[str, Any]:
    """Snapshot the starting universe for ``provenance.universe``.

    Returns ``{computed_at, well_count, wells: [...]}`` (+ ``truncated``
    when the sanity cap hit). Each well row carries the attrs the
    waterfall needs to attribute vintage/lateral/status culls without
    re-querying a warehouse that may have since resynced.
    """
    if not polygons or not formations:
        return {
            "computed_at": datetime.now(UTC).isoformat(),
            "well_count": 0,
            "wells": [],
        }

    rows = session.execute(universe_stmt(polygons, formations)).all()
    truncated = len(rows) > UNIVERSE_SANITY_CAP
    if truncated:
        log.warning(
            "universe_sanity_cap_hit",
            cap=UNIVERSE_SANITY_CAP,
            formations=formations,
            n_polygons=len(polygons),
        )
        rows = rows[:UNIVERSE_SANITY_CAP]

    wells = [
        {
            "api10": r.api10,
            "name": r.name,
            "operator": r.operator,
            "formation": r.formation_blueox,
            "first_prod_date": (r.first_prod_date.isoformat() if r.first_prod_date else None),
            "lateral_ft": float(r.lateral_ft) if r.lateral_ft is not None else None,
            "status": r.status.value if r.status is not None else None,
            # Verbatim incl. the 2800 no-neighbor sentinel — the
            # waterfall owns the sentinel semantics at read time.
            "lateral_closer_xy_ft": (
                float(r.lateral_closer_xy_ft) if r.lateral_closer_xy_ft is not None else None
            ),
        }
        for r in rows
    ]
    out: dict[str, Any] = {
        "computed_at": datetime.now(UTC).isoformat(),
        "well_count": len(wells),
        "wells": wells,
    }
    if truncated:
        out["truncated"] = True
    return out


def polygon_area_sq_mi(session: Session, polygon: dict[str, Any]) -> float | None:
    """Geodesic area of one GeoJSON polygon in square miles (header block)."""
    try:
        area_m2 = session.execute(
            select(
                func.ST_Area(text("ST_SetSRID(ST_GeomFromGeoJSON(:aoi), 4326)::geography"))
            ).params(aoi=json.dumps(polygon))
        ).scalar_one()
    except Exception:  # malformed ring — area is cosmetic, don't fail the save
        log.warning("polygon_area_failed", exc_info=True)
        return None
    if area_m2 is None:
        return None
    return round(float(area_m2) / _SQ_METERS_PER_SQ_MILE, 2)
