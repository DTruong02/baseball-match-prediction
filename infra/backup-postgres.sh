#!/usr/bin/env bash
# Dump Postgres from the Compose `db` service into a dated file under BACKUP_DIR.
# Intended for the Oracle Always Free VM (or any host running this Compose stack).
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-$HOME/baseball-chatbot}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE_PROJECT_DIR="${COMPOSE_PROJECT_DIR:-$DEPLOY_PATH}"
SERVICE="${POSTGRES_SERVICE:-db}"
DB_USER="${POSTGRES_USER:-baseball}"
DB_NAME="${POSTGRES_DB:-baseball}"

cd "${COMPOSE_PROJECT_DIR}"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx "${SERVICE}"; then
  echo "ERROR: Compose service '${SERVICE}' is not running in ${COMPOSE_PROJECT_DIR}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
outfile="${BACKUP_DIR}/baseball_${stamp}.sql.gz"

echo "==> Dumping ${DB_NAME} → ${outfile}"
docker compose exec -T "${SERVICE}" \
  pg_dump -U "${DB_USER}" -d "${DB_NAME}" --clean --if-exists \
  | gzip -c > "${outfile}"

bytes="$(wc -c < "${outfile}" | tr -d ' ')"
if [[ "${bytes}" -lt 100 ]]; then
  echo "ERROR: Backup file looks empty (${bytes} bytes)" >&2
  rm -f "${outfile}"
  exit 1
fi

echo "==> Pruning backups older than ${RETENTION_DAYS} days in ${BACKUP_DIR}"
find "${BACKUP_DIR}" -type f -name 'baseball_*.sql.gz' -mtime "+${RETENTION_DAYS}" -print -delete || true

echo "==> Backup complete (${bytes} bytes)"
