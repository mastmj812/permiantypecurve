"""Serve the self-hosted Protomaps PMTiles basemap.

PMTiles reads data via HTTP byte-range requests, so this endpoint MUST honor
the Range header (RFC 7233) and respond with 206 + Content-Range. A plain
FileResponse would force the client to download the whole file (hundreds of
MB) for every tile lookup, which defeats the format entirely.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.config import settings
from app.core.logging import get_logger

router = APIRouter(prefix="/basemap", tags=["basemap"])
log = get_logger(__name__)

# "bytes=START-END" where either side may be empty (suffix range "bytes=-N"
# is also valid but rare for PMTiles clients; we support the common form).
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 256 * 1024  # 256 KB — large enough to keep syscalls cheap.


async def _file_chunks(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    remaining = length
    with path.open("rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk = f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/permian.pmtiles")
async def serve_pmtiles(request: Request) -> Response:
    path = settings.pmtiles_path
    if not path.is_file():
        # Don't 500 — this is the expected state before first-time setup.
        # The README points users to infra/basemap/fetch.sh for this.
        log.warning("pmtiles_missing", path=str(path))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"PMTiles file not found at {path}. "
                "Run infra/basemap/fetch.sh (or fetch.ps1 on Windows) to download it."
            ),
        )

    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    common_headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "application/vnd.pmtiles",
        "Cache-Control": "public, max-age=3600",
    }

    if range_header is None:
        # Full-file fallback. Used by curl-style probes; pmtiles-js always
        # sends a Range header in practice.
        return FileResponse(path, headers=common_headers)

    match = _RANGE_RE.match(range_header.strip())
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=f"Malformed Range header: {range_header!r}",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_s, end_s = match.groups()
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else file_size - 1
    end = min(end, file_size - 1)

    if start > end or start >= file_size:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Range out of bounds",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    length = end - start + 1
    headers = {
        **common_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(
        _file_chunks(path, start, length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers,
    )


@router.head("/permian.pmtiles")
async def head_pmtiles() -> Response:
    path = settings.pmtiles_path
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    file_size = path.stat().st_size
    return Response(
        status_code=200,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Type": "application/vnd.pmtiles",
            "Content-Length": str(file_size),
        },
    )


@router.get("/plss_tx_nm.geojson")
async def serve_plss() -> Response:
    """Serve the cached BLM PLSS sections GeoJSON for TX + NM.

    Not present by default — the user opts in by downloading from
    blm.gov/services/geospatial/GISData/cadastral and placing the file at
    PLSS_GEOJSON_PATH. The frontend handles the 404 with a helpful toast.
    """
    path = settings.plss_geojson_path
    if not path.is_file():
        log.info("plss_missing", path=str(path))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"PLSS GeoJSON not found at {path}. Download from "
                "https://www.blm.gov/services/geospatial/GISData/cadastral "
                "and save as plss_tx_nm.geojson in infra/basemap."
            ),
        )
    return FileResponse(
        path,
        media_type="application/geo+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )
