#!/usr/bin/env bash
# pg_dump the Permian Postgres into infra/backup/dumps/<timestamp>.sql.gz.
# Run from the project root:  ./infra/backup/backup.sh
#
# Schedule with cron for nightlies:
#   0 2 * * *  /path/to/permian_type_curve/infra/backup/backup.sh
set -euo pipefail

KEEP_DAYS="${KEEP_DAYS:-14}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DUMPS_DIR="${SCRIPT_DIR}/dumps"
mkdir -p "${DUMPS_DIR}"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="${DUMPS_DIR}/permian_${STAMP}.sql.gz"

echo "Dumping postgres -> ${OUT_FILE}"

# `--clean --if-exists` makes the dump idempotent on restore.
docker compose exec -T postgres \
    pg_dump --clean --if-exists -U permian -d permian \
    | gzip > "${OUT_FILE}"

if [[ ! -s "${OUT_FILE}" ]]; then
    echo "Backup empty — failing." >&2
    exit 1
fi

size_mb="$(du -m "${OUT_FILE}" | cut -f1)"
echo "Wrote ${size_mb} MB"

# Prune dumps older than KEEP_DAYS.
find "${DUMPS_DIR}" -name "permian_*.sql.gz" -mtime "+${KEEP_DAYS}" -print -delete
