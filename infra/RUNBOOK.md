# Operations runbook — common failures (Stage 7.5)

Pair with [HOSTING.md](HOSTING.md) for VM bootstrap and deploy. Commands assume
you are on the host with `cd ~/baseball-chatbot` (or `$DEPLOY_PATH`) and
`COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` in `.env`.

## Quick triage

| Symptom | First checks |
|---------|----------------|
| Site down / 502 | `docker compose ps`, Caddy logs, `curl -fsS https://$DOMAIN/api/health` |
| Login / schedule empty | `curl -fsS https://$DOMAIN/api/ready`, DB logs, schedule sync |
| “Live data degraded” banner | Redis, live worker heartbeat, MLB reachability |
| No email alerts | SMTP env, `notification-worker` logs, pending rows |
| Deploy failed | `infra/deploy.sh` output; `docker compose logs --tail=100 api` |

Useful endpoints:

- `GET /health` — process up; includes `live_degraded`, `redis_ok`
- `GET /ready` — Postgres + Redis; worker heartbeats + `live_feed` marker
- `GET /metrics` — latency, errors, cache hit/miss, worker lag

```bash
docker compose ps
docker compose logs --tail=80 api worker notification-worker caddy
curl -fsS http://127.0.0.1:8000/health   # only if API published; else:
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ready').read().decode())"
```

---

## Live data degraded (Redis / MLB / stale snapshot)

**What users see:** “Live data degraded” on the home slate and game detail. The API
still serves the last Postgres `games.live_state` snapshot with `degraded=true`.

**Causes:**

1. Redis down or unreachable (`redis_ok: false` on `/health`)
2. Live worker cannot reach MLB (`checks.live_feed.ok: false` on `/ready`)
3. Snapshot older than `LIVE_STALE_AFTER_SECONDS` (default 90s) for a specific game

**Steps:**

1. Confirm Redis: `docker compose ps redis` and `docker compose exec redis redis-cli ping`
2. Confirm live worker: `docker compose logs --tail=100 worker` — look for MLB errors / rate limits
3. Check heartbeat lag on `/ready` → `checks.live_worker.lag_seconds` (large lag ⇒ worker stuck/stopped)
4. Restart if needed: `docker compose restart redis worker api`
5. If MLB is rate-limiting (429): raise spacing via `LIVE_POLL_MIN_REQUEST_INTERVAL_SECONDS` /
   `MLB_MIN_REQUEST_INTERVAL_SECONDS`, wait for the window to clear; retries already honor `Retry-After`

When Redis recovers, new polls refill the cache and `degraded` clears without a
data migration.

---

## Live worker stuck or lagging

1. `docker compose ps worker` — should be `Up`
2. `/ready` → `live_worker.lag_seconds` and last heartbeat `updated_at`
3. Logs: consecutive failure backoff doubles the poll interval (capped)
4. `docker compose restart worker`
5. If the container crash-loops: check `DATABASE_URL`, Redis, and disk space (`df -h`)

Jobs are idempotent: re-polling the same game reuses `game_events` unique
`(game_pk, event_id)` and `alert_dispatches` so events/alerts do not duplicate.

---

## MLB API outage or 429s

Outbound calls are rate-limited process-wide (`MLB_MIN_REQUEST_INTERVAL_SECONDS`,
default 0.25s) and again in the live worker (`LIVE_POLL_*`). `_get` retries 429/5xx
with exponential backoff and `Retry-After`.

1. Confirm external reachability from the VM:  
   `curl -sI https://statsapi.mlb.com/api/v1/teams?sportId=1 | head`
2. Temporarily increase poll interval / min request spacing in `.env`, then  
   `docker compose up -d worker api`
3. Schedule sync and live UI should fall back to last DB state; show degraded banner until healthy

---

## Notification / SMTP failures

1. Confirm env: `SMTP_HOST`, `SMTP_FROM`, credentials in Compose `notification-worker`
2. `docker compose logs --tail=100 notification-worker`
3. Pending emails stay `pending` until sent; transient SMTP errors are retried
   in-process (`NOTIFICATION_EMAIL_RETRIES`, default 2) then marked `failed`
4. Fix SMTP and re-enqueue failed alerts only if product requires it (failed rows
   are terminal; new alerts create new pending rows via idempotent `alert_dispatches`)

Misconfigured SMTP (`EmailNotConfiguredError`) fails immediately — set host/from.

---

## Postgres restore from backup

Backups: [`backup-postgres.sh`](backup-postgres.sh) → `~/backups/postgres/baseball_*.sql.gz`.

```bash
# Stop writers first
docker compose stop api worker notification-worker web

# Optional: take a safety dump of current state
BACKUP_DIR=/tmp/pre-restore ./infra/backup-postgres.sh

# Restore (destructive to current DB contents)
gunzip -c ~/backups/postgres/baseball_YYYYMMDDTHHMMSSZ.sql.gz \
  | docker compose exec -T db psql -U baseball -d baseball

docker compose start api worker notification-worker web
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ready').read().decode())"
```

Install daily cron (after first deploy):

```bash
mkdir -p ~/backups/postgres
chmod +x ~/baseball-chatbot/infra/backup-postgres.sh
(crontab -l 2>/dev/null; echo "15 4 * * * DEPLOY_PATH=$HOME/baseball-chatbot $HOME/baseball-chatbot/infra/backup-postgres.sh >>$HOME/backups/postgres/cron.log 2>&1") | crontab -
```

---

## Deploy rollback

1. `cd ~/baseball-chatbot && git fetch && git log --oneline -5`
2. `git checkout <known-good-sha>`
3. `./infra/deploy.sh` (or `docker compose up -d --build`)
4. Verify `/health`, `/ready`, login, one live game page

Database migrations are forward-only in normal operation; if a bad migration
shipped, restore from the pre-deploy backup before re-deploying the good SHA.

---

## Host firewall and security updates

[`host-harden.sh`](host-harden.sh) (also invoked from bootstrap) opens only
SSH/HTTP/HTTPS and enables unattended security updates (`unattended-upgrades` or
`dnf-automatic`). Re-run anytime:

```bash
sudo ./infra/host-harden.sh
```

OCI Security Lists must still allow 22/80/443. Never publish Postgres/Redis/API
ports on the public interface when using `docker-compose.prod.yml`.
