#!/usr/bin/env bash
# Remote deploy helper for the Oracle Always Free (or any) Compose host.
# Invoked by .github/workflows/deploy.yml over SSH.
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-$HOME/baseball-chatbot}"
DEPLOY_REF="${DEPLOY_REF:-origin/main}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_SLEEP_SECONDS="${HEALTH_SLEEP_SECONDS:-5}"

echo "==> Deploying baseball-chatbot in ${DEPLOY_PATH} (ref=${DEPLOY_REF})"

if [[ ! -d "${DEPLOY_PATH}/.git" ]]; then
  echo "ERROR: ${DEPLOY_PATH} is not a git checkout. Clone the repo there first." >&2
  exit 1
fi

cd "${DEPLOY_PATH}"

echo "==> Fetching and checking out ${DEPLOY_REF}"
git fetch --prune origin
git fetch --depth=1 origin "${DEPLOY_REF}" || git fetch origin "${DEPLOY_REF}"
git checkout --force "${DEPLOY_REF}"

if [[ ! -f .env ]]; then
  echo "WARN: ${DEPLOY_PATH}/.env missing; Compose will use built-in defaults." >&2
fi

echo "==> Building images"
docker compose build

echo "==> Running database migrations"
docker compose run --rm --entrypoint sh api -c \
  'cd /app/backend && alembic upgrade head'

echo "==> Starting Compose stack"
docker compose up -d --remove-orphans

echo "==> Waiting for API health at ${HEALTH_URL}"
ok=0
for i in $(seq 1 "${HEALTH_RETRIES}"); do
  if curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; then
    ok=1
    break
  fi
  echo "    attempt ${i}/${HEALTH_RETRIES} — not ready yet"
  sleep "${HEALTH_SLEEP_SECONDS}"
done

if [[ "${ok}" -ne 1 ]]; then
  echo "ERROR: API health check failed after deploy." >&2
  docker compose ps >&2 || true
  exit 1
fi

echo "==> Deploy complete"
docker compose ps
