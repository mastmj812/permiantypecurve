#!/usr/bin/env bash
# Run migrations to head, then start uvicorn. Idempotent — replays cleanly
# on every container start, no-ops once schema is current.
set -euo pipefail

echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting uvicorn"
# --timeout-graceful-shutdown caps how long a --reload restart waits for
# in-flight/keep-alive connections to drain. Without it, the map tab's
# constant .mvt tile fetches keep HTTP keep-alive sockets open, so a file
# change triggers a shutdown that never completes ("Waiting for connections
# to close") and the backend hangs — freezing the whole app.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload \
  --timeout-graceful-shutdown 5
