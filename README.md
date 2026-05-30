# Permian Type Curve Tool

ComboCurve-style workflow for generating well-level decline forecasts and
aggregated type curves from a Permian-focused data warehouse. Single-user,
local-first, Permian-focused. As of 2026-05-29 the app reads from a
separate `engineering_db` warehouse (Enverus + Novi merged at the
warehouse layer) rather than calling Enverus directly.

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
backend/    FastAPI app, models, forecasting math, warehouse_client
frontend/   Vite + React + MapLibre
infra/      docker init scripts, PMTiles fetch, PLSS overlay
docs/       architecture diagrams, decision notes
```

## Prerequisites

- The `engineering_db` warehouse must be running and populated with
  Permian curated data (see that repo's README). The type-curve app
  reads from `curated.wells_enriched` and `curated.production` over a
  network connection — no direct Enverus or Novi calls happen here.

## First-run

```powershell
# 1. Copy env template. Set JWT_SECRET to a long random string for prod.
#    Set WAREHOUSE_DATABASE_URL to point at the running engineering_db.
cp .env.example .env

# 2. Download the Texas+NM PMTiles basemap (one-time, ~150–250 MB).
.\infra\basemap\fetch.ps1          # Windows
./infra/basemap/fetch.sh           # macOS / Linux / Git Bash

# 3. Bring up the stack. Migrations run automatically on backend startup.
docker compose up --build -d

# 4. Create your user account (single-user — no signup flow in the UI).
docker compose exec backend python -m app.cli.create_user --email you@example.com
#    → prompts for password (≥ 8 chars, typed twice)

# 5. Bulk-load wells + production from the warehouse into the local DB.
#    Takes ~10–15 min for the full Permian (~61k wells, ~4.2M production rows).
docker compose exec backend python -c "from app.sync.orchestrator import sync_permian; print(sync_permian())"
```

Then in the browser:
- **Frontend**: http://localhost:5173  → log in with the email/password from step 4
- **Backend API docs**: http://localhost:8000/docs (protected endpoints require the bearer token)
- **Health probe (unauth)**: http://localhost:8000/api/health

You should land on the **Map** tab with the freshly-synced Permian wells colored by formation. The header shows your email, a green "api ok" pill, and a sign-out link.

## Environment variables

See `.env.example`. The ones that matter:

| Variable                 | Purpose                                                  |
| ------------------------ | -------------------------------------------------------- |
| `DATABASE_URL`           | SQLAlchemy URL — the local app DB; defaults to the compose Postgres |
| `WAREHOUSE_DATABASE_URL` | Read-only DSN for `engineering_db`'s `curated.*` views — required for sync |
| `JWT_SECRET`             | **Must be set in production.** HS256 signing secret      |
| `LOG_LEVEL`              | `DEBUG` / `INFO` / `WARNING`                             |
| `PMTILES_PATH`           | Absolute path to the PMTiles file inside the backend     |
| `PLSS_GEOJSON_PATH`      | Absolute path to the optional BLM PLSS GeoJSON           |
| `SENTRY_DSN`             | Optional; structlog → Sentry if set                      |

## Tests

```bash
# Backend (pure-function tests, ~100 cases including real-well baselines)
docker compose exec backend pytest

# Frontend
docker compose exec frontend npm run typecheck
docker compose exec frontend npm test          # vitest — outlier detection cases
```

## Backups

Nightly pg_dump (`infra/backup/`):

```powershell
.\infra\backup\backup.ps1                       # one-shot
# Schedule with Task Scheduler (Windows) or cron — see infra/backup/README.md
```

Restore is documented in `infra/backup/README.md`.

## Sync flow

Single path, all from the warehouse:

```bash
# Via Python (canonical entry point)
docker compose exec backend python -c "from app.sync.orchestrator import sync_permian; print(sync_permian())"

# Via the API (pollable status)
curl -X POST localhost:8000/api/sync/run \
  -H "content-type: application/json" -d '{}'
curl localhost:8000/api/sync/status
```

`sync_permian` runs two phases:
1. `well_headers` — bulk-fetch from `engineering_db.curated.wells_enriched` filtered to `first_completion_date >= 2010-01-01` AND `is_horizontal = TRUE` (entire Permian; no county filter); upserted into local `wells` keyed by **api10** (Novi 10-char wellbore identifier).
2. `production` — fetch from `curated.production` for every api10 just loaded; upserted into `production_monthly` keyed by `(api10, prod_date)`. Calendar-day rates come pre-computed from Novi upstream — no app-side rate math.

Sync state lands in `sync_jobs` + `sync_watermarks(entity, scope_key)`; both keyed on a single `scope_key = "env_region=PERMIAN"` since the sync no longer splits by county.

`sync_county` / `sync_counties` remain as deprecated back-compat wrappers (the API endpoint + the `seed_county` CLI both still work); they accept-and-ignore the `basin` / `counties` args and route to `sync_permian`. Existing scripts keep working without modification.

## Build status (where we are)

- [x] **Step 1** — monorepo scaffold, Docker Compose, PMTiles fetch +
      range-serving endpoint, blank MapLibre map with Protomaps basemap.
- [x] **Step 2** — schema + Alembic, ingest pipeline, sync orchestrator,
      heel-point + wellstick generation, calday/prodday rates with
      month-1 exception.
- [x] **Step 3** — Map page: PostGIS-backed MVT tile endpoint with filter
      composition, formation-colored wellsticks, zoom-switched
      points→lines at z9, lasso/box/click selection with server-enforced
      500-well cap, summary drawer with median lateral + vintage
      histogram + top-5 operators, PLSS overlay toggle.
- [x] **Step 4** — Forecasting module + auto-forecast page: Arps
      exponential/hyperbolic/harmonic + modified hyperbolic (continuous
      switchover at Df=8%/yr) + Duong; rate-cum NLS fitter (default) and
      rate-time alternate; oil-driven peak detection; per-well batch API
      with background jobs; SVG decline charts (Cartesian + semi-log);
      live re-render preview endpoint; manual override + lock; real-well
      regression baselines.
- [x] **Step 5** — Review page: sortable/filterable table over fits,
      EUR-per-lateral-ft outlier flag (2σ from selection median),
      per-well include/exclude checkbox feeding step-6 type-curve
      aggregation, running summary panel.
- [x] **Step 6** — Type curve aggregation, library, versioning, CSV
      export: per-month P10/P25/P50/P75/P90 + mean + well_count,
      normalized per-1000-lateral-ft, oil/gas/water; selectable
      first-prod (default, incl. ramp-up) or peak-month alignment;
      implied EUR per percentile; save / list / rename / delete;
      compare two curves on the same chart; ZIP-of-CSVs export.
- [x] **Step 7** — JWT auth (single-user with `create_user` CLI;
      structured for future SSO), bearer-protected routes, login UI +
      sign-out, pg_dump backup script with Windows / cron schedules,
      README walkthrough.
- [x] **Cutover** (2026-05-28 → 2026-05-29) — replaced direct-Enverus
      ingest with reads from the `engineering_db` warehouse. Local schema
      migrated from api14 PK to api10 PK; raw_payload / wellstick_source
      / stages / source / rate_prodday_* columns retired; legacy
      Enverus client package deleted. Single Novi-sourced 4-point
      wellstick replaces Enverus LateralLine + the heel-from-survey
      pipeline.

## Architecture diagram

See [docs/architecture.md](docs/architecture.md).
