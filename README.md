# Permian Type Curve Tool

ComboCurve-style workflow for generating well-level decline forecasts and
aggregated type curves from cached Enverus data. Single-user, local-first,
Permian-focused.

## Stack

| Layer    | Tech                                                       |
| -------- | ---------------------------------------------------------- |
| Backend  | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic              |
| DB       | PostgreSQL 16 + PostGIS + TimescaleDB (`timescale/timescaledb-ha`) |
| Frontend | React 18 + TS, Vite, TanStack Query, Zustand               |
| Map      | MapLibre GL JS + Protomaps PMTiles (self-hosted, no token) |
| Charts   | Plotly.js                                                  |
| Forecast | numpy / scipy.optimize                                     |
| Deploy   | Docker Compose                                             |

## Repo layout

```
backend/    FastAPI app, models, forecasting math, Enverus client
frontend/   Vite + React + MapLibre
infra/      docker init scripts, PMTiles fetch, PLSS overlay
docs/       architecture diagrams, decision notes
```

## First-run

```bash
# 1. Copy env template and edit as needed (Enverus keys can wait until step 2).
cp .env.example .env

# 2. Download the Texas+NM PMTiles basemap (one-time, ~150–250 MB).
./infra/basemap/fetch.sh           # macOS / Linux / Git Bash
.\infra\basemap\fetch.ps1          # Windows PowerShell

# 3. Bring up the stack.
docker compose up --build
```

Then:
- Frontend: http://localhost:5173
- Backend health: http://localhost:8000/api/health
- API docs: http://localhost:8000/docs

You should see a blank MapLibre map of the Permian rendered against the
self-hosted Protomaps basemap. A green "api ok" badge in the header
confirms the backend.

## Environment variables

See `.env.example`. The ones that matter for step 1:

| Variable          | Purpose                                              |
| ----------------- | ---------------------------------------------------- |
| `DATABASE_URL`    | SQLAlchemy URL — defaults to compose Postgres        |
| `JWT_SECRET`      | HS256 signing secret (step 7)                        |
| `LOG_LEVEL`       | `DEBUG` / `INFO` / `WARNING`                         |
| `PMTILES_PATH`    | Absolute path to the PMTiles file inside the backend |
| `SENTRY_DSN`      | Optional; structlog → Sentry if set                  |

Step 2 adds:
- `ENVERUS_API_KEY_PRISM` — Prism API key
- `ENVERUS_API_KEY_DI`    — DirectAccess fallback key

## Tests

```bash
# Backend
docker compose exec backend pytest

# Frontend
docker compose exec frontend npm run typecheck
docker compose exec frontend npm test
```

## Seed flow

Two paths — pick whichever you have credentials for. Both exercise the same
ingest pipeline (header upsert → rate calc with month-1 exception → heel
point → wellstick LINESTRING in PostGIS → watermarks).

```bash
# Bring the stack up first (migrations run automatically).
docker compose up --build

# --- Path A: synthetic (no Enverus credentials needed) ---
# 50 deterministic Loving County wells with realistic vintages, formations,
# laterals, surveys, and Arps-decline production. Use this to develop and
# test steps 3+ before real keys arrive.
docker compose exec backend python -m app.seed.seed_synthetic --n 50 --seed 42

# --- Path B: real Enverus Prism pull ---
# Requires ENVERUS_API_KEY_PRISM in .env. Validate the field-name
# assumptions in app/enverus_client/prism.py (_PATH_* + _parse_*) before
# the first pull — these are placeholders until live payloads land.
docker compose exec backend python -m app.seed.seed_county --county Loving

# --- Via the API (either path; status is pollable) ---
curl -X POST localhost:8000/api/sync/run \
  -H "content-type: application/json" \
  -d '{"basin":"Permian","county":"Loving"}'
curl localhost:8000/api/sync/status
```

Sync runs three phases per county:
1. `well_headers` — full bulk pull, upserted by api14
2. `production`   — monthly volumes; calday + prodday rates computed on write
3. `surveys`      — per-well; on each insert, heel point + wellstick LINESTRING are recomputed in PostGIS

Watermarks land in `sync_watermarks(entity, scope_key)` and feed the next
incremental pull via `updated_since`.

The synthetic seeder deliberately includes edge-case wells so the map
renders all wellstick variants:
* 2 wells with no survey → `wellstick_source = surface_to_bh`
* 1 well that's never above 80° inclination → `surface_to_bh` fallback
* 1 well with a malformed high-inclination station → second high-incl station wins

## Build status (where we are)

- [x] **Step 1** — monorepo scaffold, Docker Compose, PMTiles fetch +
      range-serving endpoint, blank MapLibre map with Protomaps basemap.
- [x] **Step 2** — schema + Alembic, Enverus Prism client (HTTP-mocked
      tests), Loving County seed CLI + `/api/sync/run`, heel-point +
      wellstick generation, calday/prodday rates with month-1 exception.
- [x] **Step 3** — Map page: PostGIS-backed MVT tile endpoint with filter
      composition, formation-colored wellsticks (solid/dashed for survey
      vs no-survey), zoom-switched points→lines at z9, lasso/box/click
      selection with server-enforced 500-well cap, summary drawer with
      median lateral + vintage histogram + top-5 operators, PLSS overlay
      toggle.
- [x] **Step 4** — Forecasting module + auto-forecast page: Arps
      exponential/hyperbolic/harmonic + modified hyperbolic (continuous
      switchover at Df=8%/yr) + Duong; rate-cum NLS fitter (default) and
      rate-time alternate; oil-driven peak detection; per-well batch API
      with background jobs; SVG decline charts (Cartesian + semi-log);
      live re-render preview endpoint; manual override + lock; real-well
      regression baselines.
- [ ] Step 5 — Review page with outlier flagging.
- [ ] Step 6 — Type curve aggregation, library, CSV export, versioning.
- [ ] Step 7 — Auth, polish, docs, deploy scripts.

## Architecture diagram

See [docs/architecture.md](docs/architecture.md).
