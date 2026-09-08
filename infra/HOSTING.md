# Oracle Cloud Always Free — full Compose hosting

Run the entire baseball-chatbot stack (API, live worker, notification worker, Next.js, Postgres, Redis, Caddy) on **one** Always Free VM. WebSockets and Redis stay colocated; no sleep timers from stitching free PaaS tiers.

For the step-by-step runbook, start at [Provisioning](#1-provision-the-vm).

## Why this shape

| Approach | Fit for this app |
|----------|------------------|
| **Oracle Always Free VM + Compose** (this doc) | Full modular monolith, live worker + Redis + WS on one host |
| Split free PaaS (Vercel + Render + Neon + Upstash) | Sleep limits, harder real-time fan-out, more secret plumbing |

## Resource sizing

Prefer an **Ampere A1** (aarch64) Always Free shape with headroom for Postgres, Redis, and sklearn inference:

| Shape | Guidance |
|-------|----------|
| **Recommended** | **2–4 OCPU**, **12–24 GB** RAM (Ampere A1 Flex within Always Free quota) |
| Minimum demo | 1 OCPU / 6 GB — tight; expect slow image builds and memory pressure under live polls |
| Boot volume | ≥ 50 GB (images + Postgres volume + artifacts/cache) |
| OS | Ubuntu 22.04/24.04 or Oracle Linux 8/9 |

All base images used here (`python:3.12-slim`, `node:22-alpine`, `postgres:16-alpine`, `redis:7-alpine`, `caddy:2-alpine`) publish **arm64** variants, so Ampere works without special Dockerfiles.

## Public URL layout

Caddy terminates TLS and routes a **single domain**:

| Path | Upstream |
|------|----------|
| `https://$DOMAIN/` | Next.js (`web:3000`) |
| `https://$DOMAIN/api/*` | FastAPI (`api:8000`); `/api` prefix stripped |

Set `NEXT_PUBLIC_API_URL=https://$DOMAIN/api` so the browser and WebSockets (`wss://…/api/ws/…`) hit the same origin.

## 1. Provision the VM

1. Create an Oracle Cloud tenancy and open **Compute → Instances → Create**.
2. Choose an Always Free–eligible **Ampere A1** shape (or AMD micro if Ampere quota is exhausted).
3. Attach a public IP (or plan on [Cloudflare Tunnel](#optional-cloudflare-tunnel) instead).
4. Add an SSH key; note the `opc` (Oracle Linux) or `ubuntu` user.
5. In the VCN **Security List** (and NSG if used), allow ingress:
   - **22/tcp** — SSH (optionally lock to your IP)
   - **80/tcp**, **443/tcp** — Caddy / Let’s Encrypt
6. Point a DNS **A** (or **AAAA**) record for `$DOMAIN` at the instance public IP.

## 2. Bootstrap the host

SSH in, then:

```bash
# From your laptop, copy the bootstrap script, or curl raw from the repo after cloning.
curl -fsSL https://raw.githubusercontent.com/<you>/baseball-chatbot/main/infra/oracle-bootstrap.sh -o oracle-bootstrap.sh
chmod +x oracle-bootstrap.sh
REPO_URL=https://github.com/<you>/baseball-chatbot.git ./oracle-bootstrap.sh
```

Or clone first, then:

```bash
cd ~/baseball-chatbot
chmod +x infra/oracle-bootstrap.sh
./infra/oracle-bootstrap.sh
```

Log out and back in if you were added to the `docker` group.

## 3. Production `.env`

```bash
cd ~/baseball-chatbot
cp .env.example .env
```

Set at least:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml

DOMAIN=baseball.example.com
NEXT_PUBLIC_API_URL=https://baseball.example.com/api
CORS_ORIGINS=https://baseball.example.com

SECRET_KEY=<long-random-string>
POSTGRES_PASSWORD=<url-safe-password>

# Optional
LLM_API_KEY=
SMTP_HOST=
SMTP_FROM=
```

Use a **URL-safe** Postgres password (alphanumeric) so `DATABASE_URL` embedding stays simple. Rebuild the web image whenever `NEXT_PUBLIC_API_URL` changes.

## 4. Bring the stack up

```bash
cd ~/baseball-chatbot
docker compose up -d --build
docker compose ps
docker compose exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ready').read().decode())"
```

Or run [`infra/deploy.sh`](deploy.sh) after the first clone (also used by GitHub Actions).

Open `https://$DOMAIN`, `https://$DOMAIN/api/health`, and `https://$DOMAIN/api/ready`.

Register a trained model into the VM’s `artifacts/` mount and activate it (same commands as local; see root README).

## 5. Host firewall and security updates

Prefer the idempotent hardener (also run from bootstrap when `HARDEN_HOST=1`):

```bash
sudo ./infra/host-harden.sh
```

That opens only SSH / HTTP / HTTPS and enables unattended security updates
(`unattended-upgrades` on Ubuntu, `dnf-automatic` on Oracle Linux).

Manual equivalents — OCI Security Lists alone are not always enough; Oracle Linux often has `firewalld`:

```bash
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

On Ubuntu with `ufw`:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Do **not** open 5432, 6379, 8000, or 3000 publicly. The prod Compose file keeps those services off the host publish list.

## 6. Postgres backups

[`backup-postgres.sh`](backup-postgres.sh) dumps the Compose `db` service to
`~/backups/postgres/baseball_*.sql.gz` and prunes files older than
`RETENTION_DAYS` (default 14). Bootstrap installs a daily cron at 04:15 UTC when
`INSTALL_BACKUP_CRON=1`.

```bash
./infra/backup-postgres.sh
# Restore steps: infra/RUNBOOK.md → Postgres restore
```

## Operational runbooks

Failure recovery (degraded live feed, worker lag, MLB 429, SMTP, restore, rollback):
see **[RUNBOOK.md](RUNBOOK.md)**.

## Optional: Cloudflare Tunnel

If you prefer HTTPS **without** opening inbound 80/443 on the VM:

1. Install `cloudflared` on the VM and create a tunnel to `http://127.0.0.1:80` (Caddy) **or** directly to `web:3000` / path rules — simplest is still Caddy on the Docker network and tunnel to published `:80` on localhost only.
2. For a localhost-only Caddy publish, temporarily bind Caddy ports to `127.0.0.1:80:80` in an override and skip OCI ingress for 80/443.
3. Set `DOMAIN` / `NEXT_PUBLIC_API_URL` to the Cloudflare hostname; TLS is at the tunnel edge.

## GitHub Actions deploy

After the VM is up and `~/baseball-chatbot` has a production `.env` with `COMPOSE_FILE=…prod…`:

1. Repo secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` (optional `DEPLOY_PORT`, `DEPLOY_PATH`).
2. Variable `ENABLE_DEPLOY=true` for pushes to `main`, or use **Actions → Deploy → Run workflow**.
3. [`infra/deploy.sh`](deploy.sh) fetches the commit, builds, migrates, and health-checks the API **inside** the Compose network.

## Quick verification checklist

- [ ] `docker compose ps` — db, redis, api, worker, notification-worker, web, caddy healthy/up
- [ ] `https://$DOMAIN/api/health` returns `"status":"ok"` (liveness)
- [ ] `https://$DOMAIN/api/ready` returns `"status":"ready"` (DB + Redis)
- [ ] `https://$DOMAIN/api/metrics` returns Prometheus text (latency, errors, cache, worker lag)
- [ ] Login / today’s schedule loads in the browser
- [ ] Live game page connects (WS or polling fallback)
- [ ] OCI + host firewall: only 22/80/443 from the public internet
- [ ] `~/backups/postgres` has a recent `baseball_*.sql.gz` (or cron installed)
- [ ] Unattended security updates enabled (`unattended-upgrades` or `dnf-automatic`)

## Observability (Stage 7.4)

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness — process up; optional Redis ping |
| `GET /ready` | Readiness — Postgres `SELECT 1`, Redis when enabled; reports worker heartbeats and cache counters |
| `GET /metrics` | Prometheus exposition: HTTP latency/error rate, live cache hit/miss, worker lag |

API and workers emit **JSON logs** by default (`LOG_JSON=true`, `LOG_LEVEL=INFO`). Each HTTP response includes `X-Request-Id`.

Workers write Redis heartbeats (`worker:live:heartbeat`, `worker:notification:heartbeat`). Lag gauges on `/metrics` and `/ready` are derived from those timestamps.

**DB indexes (review):** hot paths already covered (`games.game_date`, `game_pk`, events by sequence, notifications by channel/status). Migration `20260908_0008` adds `(model_versions.kind, status)`, `players.team_id`, `(games.status, game_date)`, `predictions.model_version_id`, and `teams.abbreviation`.

**Light load test** (from a machine that can reach the API):

```bash
pip install -e ".[backend]"
python scripts/load_test.py --base-url http://127.0.0.1:8000 --concurrency 20 --requests 200
python scripts/load_test.py --base-url http://127.0.0.1:8000 --ws-game-pk <GAME_PK> --ws-clients 5 --ws-duration 10
```

On production, prefer scraping `/metrics` from the Docker network (or restrict `/api/metrics` at the edge) rather than exposing it broadly without auth.
