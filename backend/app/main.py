from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import basemap, forecasts, health, sync
from app.wells_api import detail as wells_detail
from app.wells_api import selection as wells_selection
from app.wells_api import tiles as wells_tiles
from app.config import settings
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    log = get_logger("app.main")
    log.info(
        "startup",
        version=__version__,
        pmtiles_path=str(settings.pmtiles_path),
        pmtiles_present=settings.pmtiles_path.is_file(),
    )
    yield
    log.info("shutdown")


app = FastAPI(
    title="Permian Type Curve API",
    version=__version__,
    lifespan=lifespan,
)

# Single-user dev: frontend on :5173 talks to backend on :8000 in some local
# setups. In docker compose the Vite proxy makes this unnecessary, but
# keeping it open for local non-docker workflows.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(basemap.router, prefix="/api")
app.include_router(sync.router, prefix="/api")
# All four wells_api routers share the /api/wells prefix; FastAPI merges
# them by path so /tiles, /filters/*, /select, /summary, /{api14} coexist.
app.include_router(wells_tiles.router, prefix="/api")
app.include_router(wells_detail.router, prefix="/api")
app.include_router(wells_selection.router, prefix="/api")
app.include_router(forecasts.router, prefix="/api")
