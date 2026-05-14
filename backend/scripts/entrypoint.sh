#!/usr/bin/env bash
# Run migrations to head, then start uvicorn. Idempotent — replays cleanly
# on every container start, no-ops once schema is current.
set -euo pipefail

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting uvicorn"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
