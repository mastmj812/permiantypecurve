"""Deal-acreage polygon endpoints.

  POST /api/deals/polygons/upload-shapefile
      body: multipart .zip containing .shp + .dbf (+ .prj if available)
      → parse shapes, reproject to EPSG:4326 via PostGIS ST_Transform,
        insert one row per feature with deal_id=NULL (user links via
        the admin modal afterward).

  GET  /api/deals/polygons
      → list all polygons with their attributes + assigned deal name.
        Drives the admin modal.

  GET  /api/deals/polygons.geojson
      → FeatureCollection for the Map tab. Properties: id, name,
        deal_id, deal_name, color. Raw shapefile attributes are
        excluded to keep the payload light.

  PATCH /api/deals/polygons/{id}
      body: {deal_id: uuid | null}
      → link or unlink a polygon to/from a deal.

  DELETE /api/deals/polygons/{id}
      → remove a polygon.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from typing import Any

import shapefile  # pyshp
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from pyproj import CRS
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Deal, DealPolygon
from app.db.session import get_session

router = APIRouter(prefix="/deals/polygons", tags=["deals"])
log = get_logger("api.deal_polygons")


# Stable 8-color palette keyed off a hash of the deal_id. The frontend
# mirrors this list and chooses the same color for the same deal, so
# the per-deal toggle UI and the map fill agree without an explicit
# color column.
_DEAL_PALETTE: list[str] = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#17becf",
]


def _color_for_deal(deal_id: uuid.UUID | None) -> str:
    """Deterministic palette pick. Unlinked polygons get index 0's
    color too, but the frontend renders them with a separate dashed
    outline so they read as 'unassigned'."""
    if deal_id is None:
        return "#9ca3af"  # neutral grey for unlinked
    digest = hashlib.md5(str(deal_id).encode("utf-8")).digest()
    return _DEAL_PALETTE[digest[0] % len(_DEAL_PALETTE)]


# =========================== schemas ===========================


class DealPolygonRow(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID | None
    deal_name: str | None
    name: str
    attributes: dict[str, Any]
    source_file: str | None


class UploadResponse(BaseModel):
    inserted: int
    polygon_ids: list[uuid.UUID]
    source_epsg: int | None
    source_file: str


class PatchPolygonRequest(BaseModel):
    deal_id: uuid.UUID | None


# =========================== shapefile parse ===========================


def _resolve_source_epsg(prj_text: str | None) -> int | None:
    """Best-effort EPSG identification from the .prj WKT. Returns None
    when no .prj is present or the projection can't be matched — caller
    falls back to assuming the geometry is already in EPSG:4326 and
    logs a warning."""
    if not prj_text:
        return None
    try:
        crs = CRS.from_wkt(prj_text)
    except Exception:
        return None
    code = crs.to_epsg()
    return int(code) if code is not None else None


def _shape_to_wkt(shp: shapefile.Shape) -> str | None:
    """Convert pyshp's shape (POLYGON or MULTIPOLYGON) to a
    MULTIPOLYGON WKT so the geom column type is uniform.

    pyshp returns a single ``parts`` list with start indices into a
    flat ``points`` list. A single ring is an outer boundary; subsequent
    rings can be inner holes — but for parcel/acreage shapefiles
    multiple parts are typically separate outer polygons (the shapefile
    spec uses CCW vs CW orientation to distinguish; we don't enforce
    here, just emit one polygon per part with no holes).
    """
    if not shp.points or not shp.parts:
        return None
    parts = list(shp.parts) + [len(shp.points)]
    polygons: list[str] = []
    for k in range(len(parts) - 1):
        ring = shp.points[parts[k] : parts[k + 1]]
        if len(ring) < 4:
            continue
        # Close the ring if pyshp didn't already (some shapefiles omit
        # the trailing duplicate point).
        if ring[0] != ring[-1]:
            ring = list(ring) + [ring[0]]
        coords = ", ".join(f"{x} {y}" for x, y in ring)
        polygons.append(f"(({coords}))")
    if not polygons:
        return None
    return f"MULTIPOLYGON ({', '.join(polygons)})"


def _pick_label(attributes: dict[str, Any], fid: int) -> str:
    """First non-empty string-ish attribute makes the polygon's label;
    falls back to ``Polygon {fid}`` when the attribute table is all
    numbers or empty."""
    for k, v in attributes.items():
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        # Skip pure numbers (FID / OBJECTID / acres etc.) — those
        # aren't great UI labels.
        try:
            float(s)
            continue
        except ValueError:
            return s[:255]
    return f"Polygon {fid}"


def _parse_shapefile_zip(
    zip_bytes: bytes,
) -> tuple[list[tuple[str, str, dict[str, Any]]], int | None]:
    """Return (features, source_epsg). Each feature is
    (name, wkt, attributes). source_epsg is None when the .prj was
    missing or unrecognized — caller logs + assumes 4326.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Tolerate nesting: many GIS exports zip the files under a
        # top-level directory. Match by suffix.
        members = zf.namelist()
        shp_name = next((m for m in members if m.lower().endswith(".shp")), None)
        dbf_name = next((m for m in members if m.lower().endswith(".dbf")), None)
        prj_name = next((m for m in members if m.lower().endswith(".prj")), None)
        if shp_name is None or dbf_name is None:
            raise HTTPException(
                status_code=400,
                detail="zip must contain both a .shp and a .dbf file",
            )
        shp_bytes = io.BytesIO(zf.read(shp_name))
        dbf_bytes = io.BytesIO(zf.read(dbf_name))
        prj_text = zf.read(prj_name).decode("utf-8", errors="replace") if prj_name else None

    source_epsg = _resolve_source_epsg(prj_text)
    if source_epsg is None and prj_text:
        log.warning(
            "deal_polygon_unknown_crs",
            prj_excerpt=prj_text[:120],
            note="falling back to EPSG:4326",
        )

    features: list[tuple[str, str, dict[str, Any]]] = []
    with shapefile.Reader(shp=shp_bytes, dbf=dbf_bytes) as reader:
        field_names = [f[0] for f in reader.fields[1:]]  # skip DeletionFlag
        for fid, sr in enumerate(reader.shapeRecords()):
            wkt = _shape_to_wkt(sr.shape)
            if wkt is None:
                continue
            attrs = {
                k: _jsonable(v)
                for k, v in zip(field_names, list(sr.record))
            }
            label = _pick_label(attrs, fid)
            features.append((label, wkt, attrs))
    return features, source_epsg


def _jsonable(v: Any) -> Any:
    """Coerce DBF cell values into JSON-safe primitives. pyshp returns
    datetime.date for date fields and bytes for binary; everything
    else lands as str/int/float."""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="replace")
        except Exception:
            return None
    return str(v)


# =========================== endpoints ===========================


@router.post("/upload-shapefile", response_model=UploadResponse)
async def upload_shapefile(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> UploadResponse:
    """Parse the uploaded zip and insert one ``deal_polygons`` row per
    feature. All polygons land with ``deal_id=NULL``; the user links
    them via PATCH from the admin modal afterwards."""
    raw = await file.read()
    try:
        features, source_epsg = _parse_shapefile_zip(raw)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("shapefile_parse_failed", filename=file.filename)
        raise HTTPException(
            status_code=400, detail=f"failed to parse shapefile: {e}"
        ) from e

    if not features:
        raise HTTPException(
            status_code=400,
            detail="no polygon features found in the shapefile",
        )

    # PostGIS handles the reprojection at insert time. When source_epsg
    # is unknown we assume 4326 and skip the transform — better than
    # silently emitting bogus coordinates.
    effective_src_epsg = source_epsg if source_epsg is not None else 4326

    # Override semantics: re-uploading a shapefile with the same filename
    # replaces its previous features instead of stacking duplicates. Runs
    # in the same transaction as the inserts below, so a parse/insert
    # failure rolls the delete back too.
    replaced = 0
    if file.filename:
        replaced = (
            session.execute(
                delete(DealPolygon).where(
                    DealPolygon.source_file == file.filename
                )
            ).rowcount
            or 0
        )

    # ST_Transform is a no-op when src_epsg == 4326, so one SQL covers
    # both branches. ST_Multi normalizes any POLYGON output back to
    # MULTIPOLYGON so the geom column's type constraint is satisfied
    # whether the feature came in as POLYGON or MULTIPOLYGON.
    insert_sql = text(
        "INSERT INTO deal_polygons (id, deal_id, name, geom, "
        "attributes, source_file) VALUES "
        "(:id, NULL, :name, "
        "ST_Multi(ST_Transform(ST_GeomFromText(:wkt, :src_epsg), 4326)), "
        "CAST(:attributes AS jsonb), :source_file)"
    )
    polygon_ids: list[uuid.UUID] = []
    for name, wkt, attributes in features:
        new_id = uuid.uuid4()
        session.execute(
            insert_sql,
            {
                "id": str(new_id),
                "name": name,
                "wkt": wkt,
                "src_epsg": effective_src_epsg,
                "attributes": json.dumps(attributes),
                "source_file": file.filename,
            },
        )
        polygon_ids.append(new_id)
    session.commit()
    log.info(
        "deal_polygons_uploaded",
        count=len(polygon_ids),
        replaced=replaced,
        source_epsg=source_epsg,
        filename=file.filename,
    )
    return UploadResponse(
        inserted=len(polygon_ids),
        polygon_ids=polygon_ids,
        source_epsg=source_epsg,
        source_file=file.filename or "",
    )


@router.get("", response_model=list[DealPolygonRow])
def list_polygons(
    session: Session = Depends(get_session),
) -> list[DealPolygonRow]:
    """All polygons with their assigned deal (if any). Powers the
    admin modal — geometry isn't included to keep the payload small."""
    rows = session.execute(
        select(
            DealPolygon.id,
            DealPolygon.deal_id,
            Deal.name.label("deal_name"),
            DealPolygon.name,
            DealPolygon.attributes,
            DealPolygon.source_file,
        ).outerjoin(Deal, Deal.id == DealPolygon.deal_id)
        .order_by(Deal.name.nullslast(), DealPolygon.name)
    ).all()
    return [
        DealPolygonRow(
            id=r.id,
            deal_id=r.deal_id,
            deal_name=r.deal_name,
            name=r.name,
            attributes=r.attributes or {},
            source_file=r.source_file,
        )
        for r in rows
    ]


@router.get(".geojson")
def list_polygons_geojson(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """FeatureCollection for the Map tab + SlideMap. Properties carry
    the source_file (the uploaded shapefile) so the frontend can filter
    per-shapefile visibility, plus legacy deal fields the map ignores."""
    rows = session.execute(
        select(
            DealPolygon.id,
            DealPolygon.deal_id,
            Deal.name.label("deal_name"),
            DealPolygon.name,
            DealPolygon.source_file,
            func.ST_AsGeoJSON(DealPolygon.geom).label("geom_json"),
        ).outerjoin(Deal, Deal.id == DealPolygon.deal_id)
    ).all()
    features = []
    for r in rows:
        if r.geom_json is None:
            continue
        try:
            geometry = json.loads(r.geom_json)
        except json.JSONDecodeError:
            continue
        features.append(
            {
                "type": "Feature",
                "id": str(r.id),
                "geometry": geometry,
                "properties": {
                    "id": str(r.id),
                    "name": r.name,
                    # The uploaded shapefile this feature came from —
                    # drives per-shapefile show/hide on the Map tab.
                    "source_file": r.source_file,
                    # Legacy deal fields, retained for back-compat with
                    # any consumer that still reads them; the map no
                    # longer uses deal assignment.
                    "deal_id": str(r.deal_id) if r.deal_id else None,
                    "deal_name": r.deal_name,
                    "color": _color_for_deal(r.deal_id),
                    "visibility_key": str(r.deal_id) if r.deal_id else "_unlinked",
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.patch("/{polygon_id}", response_model=DealPolygonRow)
def patch_polygon(
    polygon_id: uuid.UUID,
    req: PatchPolygonRequest,
    session: Session = Depends(get_session),
) -> DealPolygonRow:
    """Link (or unlink: deal_id=null) a polygon to a deal."""
    poly = session.get(DealPolygon, polygon_id)
    if poly is None:
        raise HTTPException(status_code=404, detail="polygon not found")
    if req.deal_id is not None:
        deal = session.get(Deal, req.deal_id)
        if deal is None:
            raise HTTPException(status_code=400, detail="deal not found")
    poly.deal_id = req.deal_id
    session.commit()
    deal_name = (
        session.get(Deal, poly.deal_id).name if poly.deal_id else None
    )
    return DealPolygonRow(
        id=poly.id,
        deal_id=poly.deal_id,
        deal_name=deal_name,
        name=poly.name,
        attributes=poly.attributes or {},
        source_file=poly.source_file,
    )


# Registered BEFORE the /{polygon_id} route so "by-source-file" isn't
# captured as a UUID path param (which would 422 before reaching here).
@router.delete("/by-source-file", status_code=200)
def delete_by_source_file(
    source_file: str,
    session: Session = Depends(get_session),
) -> dict[str, int]:
    """Delete every polygon from one uploaded shapefile (matched by
    source_file). Powers the manage modal's per-group 'delete shapefile'
    action so the user can clear a whole upload in one click."""
    deleted = (
        session.execute(
            delete(DealPolygon).where(DealPolygon.source_file == source_file)
        ).rowcount
        or 0
    )
    session.commit()
    return {"deleted": deleted}


@router.delete("/{polygon_id}", status_code=204)
def delete_polygon(
    polygon_id: uuid.UUID, session: Session = Depends(get_session)
) -> None:
    session.execute(
        delete(DealPolygon).where(DealPolygon.id == polygon_id)
    )
    session.commit()
