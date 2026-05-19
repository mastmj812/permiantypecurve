from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import auth, basemap, deals, forecasts, health, sync, type_curves
from app.api.auth import get_current_user
from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.wells_api import detail as wells_detail
from app.wells_api import selection as wells_selection
from app.wells_api import tiles as wells_tiles


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

# Public routes (no auth):
#   * /health        — liveness probe; the frontend's HealthBadge polls this
#                      pre-login to render the green pill on the login page.
#   * /auth/*        — login/logout/me; obviously must accept unauth requests.
#   * /basemap/*     — PMTiles + PLSS GeoJSON. Static assets served raw so
#                      MapLibre can range-request without an Authorization
#                      header (its tile fetcher doesn't attach one anyway).
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(basemap.router, prefix="/api")

# Protected routes — applied as a router-level dependency so every endpoint
# on each router requires a valid bearer token. 401 on missing/invalid/expired.
protected = [Depends(get_current_user)]
app.include_router(sync.router, prefix="/api", dependencies=protected)
app.include_router(wells_tiles.router, prefix="/api", dependencies=protected)
app.include_router(wells_detail.router, prefix="/api", dependencies=protected)
app.include_router(wells_selection.router, prefix="/api", dependencies=protected)
app.include_router(forecasts.router, prefix="/api", dependencies=protected)
app.include_router(type_curves.router, prefix="/api", dependencies=protected)
app.include_router(deals.router, prefix="/api", dependencies=protected)
