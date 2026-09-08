#!/bin/sh
set -eu

echo "Running database migrations..."
cd /app/backend
alembic upgrade head
cd /app

echo "Starting API..."
exec uvicorn baseball_backend.main:app --host 0.0.0.0 --port "${API_PORT:-8000}"
