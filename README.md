# baseball-chatbot — Pregame win probability (Python)

Installable package **`baseball-analyze`** that builds pregame features from the [MLB Stats API](https://statsapi.mlb.com/) and FanGraphs data via [`pybaseball`](https://github.com/jldbc/pybaseball), then predicts **P(home team wins)** with scikit-learn.

## Install

```bash
cd baseball-chatbot
pip install -e ".[dev]"
```

## Package layout

ML code lives under `src/baseball_analyze/`:

| Path | Role |
|------|------|
| `data/` | MLB client, FanGraphs loaders, cache, park factors, team mapping |
| `features/` | Pregame feature engineering (`FEATURE_COLUMNS`, `build_features_for_game`); in-game WP features in `features/in_game.py` |
| `models/` | Training (pregame + in-game), sklearn artifact I/O, `predict_core`, **`inference.predict_game`** |
| `configs/` (repo root) | YAML training configs |
| `cli.py`, `chat_tools.py`, `chat_repl.py` | CLI and grounded chat REPL |

Thin re-exports at legacy paths (e.g. `baseball_analyze.mlb_client`) remain for compatibility; prefer the paths above.

## Train a model

Training downloads season schedules from MLB, keeps **Final / Completed Early** games only, then makes **two HTTP calls per game** (linescore + box score) plus Savant season tables (cached under `./cache/`). See `--help` for all flags.

**Config-driven training** (CLI flags override YAML values):

```bash
baseball-analyze-train --config configs/logistic_regression.yaml
baseball-analyze-train --config configs/logistic_regression.yaml --max-games 400 --seasons 2023
```

**Mid-season (include finished games from the current year):**

The default config trains on `2023–2026` Final games and holds out games on/after `val_from_date` for validation. Unfinished / future schedule rows are skipped automatically. Optional `--through-date` caps the pool for a reproducible cutoff.

```bash
# Use defaults from configs/logistic_regression.yaml (seasons include 2026 + val_from_date)
baseball-analyze-train --config configs/logistic_regression.yaml

# Or override on the CLI
baseball-analyze-train --seasons 2023,2024,2025,2026 \
  --val-from-date 2026-08-01 --through-date 2026-09-09 --calibrate
```

`val_from_date` takes precedence over `val_seasons` when both are set. Current-year Savant tables are cache-keyed by UTC date so YTD stats refresh daily; finished seasons keep a stable `final` cache key.

**Legacy-style flags** (no config file):

```bash
baseball-analyze-train --seasons 2023 --max-games 300 --out artifacts/model.joblib
```

### Versioned artifacts

Each training run writes a versioned directory under `artifacts/<run_id>/`:

- `model.joblib` — fitted sklearn pipeline
- `metrics.json` — accuracy, ROC-AUC, log loss, Brier
- `manifest.json` — feature columns, seasons, hyperparameters, `created_at`, optional git hash

A convenience copy is also written to the configured `out` path (default `artifacts/model.joblib`) so the CLI and chat keep working without passing a run id.

### Training accuracy notes

Pregame training defaults to **as-of-game-day** team/pitcher stats (MLB
``byDateRange`` through the day before each game) and **schedule probable**
starters (with box-score fallback when a probable is missing). Holdout metrics
are closer to real pregame conditions than the old end-of-season / box-score
pipeline, but still not a full market-grade backtest:

1. **Early-season sparsity** — As-of windows before enough games have been played
   use thin samples and neutral fallbacks.
2. **Historical probables** — MLB schedule hydrate usually retains listed
   starters; when missing we fall back to the box-score starter.
3. **Park factors** — Defaults in `data/park_data.py` with light season overlays;
   refresh from FanGraphs for exact current-year park precision.

Retrain after pulling these changes — `FEATURE_COLUMNS` grew (xFIP, rest days,
recent starter FIP), so older `model.joblib` artifacts will fail the feature
mismatch check.

## Train an in-game (live WP) model

Stage 5 uses a **separate** feature set and artifact from pregame. Training walks completed games, rebuilds pre-PA game state from play-by-play, and labels every row with the game's final home-win outcome.

```bash
baseball-analyze-train-in-game --config configs/in_game_logistic_regression.yaml
baseball-analyze-train-in-game --config configs/in_game_logistic_regression.yaml --max-games 50 --seasons 2023
# Mid-season: finished current-year games + date holdout (see YAML defaults)
baseball-analyze-train-in-game --config configs/in_game_logistic_regression.yaml --max-games 50
```

Artifacts use the same layout (`artifacts/<run_id>/{model.joblib,metrics.json,manifest.json}`). The convenience copy defaults to `artifacts/in_game_model.joblib`. Manifests include `"kind": "in_game"` and `IN_GAME_FEATURE_COLUMNS`.

Register and activate **independently** of the pregame model (activating in-game does not archive pregame):

```bash
baseball-register-model <run_id> --activate
# or explicitly:
baseball-register-model <run_id> --kind in_game --activate
```

## Predict (pregame)

Requires a trained model (default `artifacts/model.joblib`; any versioned `artifacts/<run_id>/model.joblib` also works).

```bash
baseball-analyze predict --date 2025-04-06 --model artifacts/model.joblib
baseball-analyze predict --game-pk 778285 --explain
```

## Chat (LLM-backed, grounded)

The chat REPL uses an LLM to understand your question, then calls local tools that fetch schedules and run your trained sklearn model. It will not invent probabilities; it only prints numbers produced by the model artifact.

Install the optional dependency:

```bash
pip install -e ".[dev,chat]"
```

Cloud (OpenAI):

```bash
set OPENAI_API_KEY=your_key_here
baseball-analyze chat --model artifacts/model.joblib
```

Local (OpenAI-compatible server, e.g. Ollama):

```bash
set LLM_BASE_URL=http://localhost:11434/v1
set LLM_MODEL=llama3.1
set OPENAI_API_KEY=ollama
baseball-analyze chat --model artifacts/model.joblib
```

Configuration can also be set through CLI flags. See `--help` for more.

## Library usage

**Single-game inference** (CLI and chat use this internally):

```python
from baseball_analyze.models.inference import predict_game

result = predict_game(778285, "artifacts/model.joblib")
# home_win_proba, away_win_proba, features, model_version, notes, game_pk, ...
```

**Live (in-game) inference** from a live feed snapshot:

```python
from baseball_analyze.models.inference import predict_in_game

result = predict_in_game(
    live_feed,
    "artifacts/in_game_model.joblib",
    game_pk=778285,
    season=2025,
    home_abbrev="NYY",
    away_abbrev="BOS",
)
```

The live worker re-runs this on meaningful events (runs, outs, pitching changes, end of inning), upserts a `Prediction` for the active `in_game` model, and pushes `home_win_proba` / `away_win_proba` on the Redis/WebSocket live snapshot. When home WP moves by ≥5 percentage points, a rule-based `wp_explanation` is attached (e.g. `NYY scored 3 runs; WP +18%`). `GET /games/{game_pk}` returns both `pregame_prediction` and `live_prediction`.

**Feature construction** (custom workflows):

```python
from baseball_analyze.data.mlb_client import fetch_schedule_for_date
from baseball_analyze.features import build_features_for_game, FEATURE_COLUMNS
from baseball_analyze.models.model import load_artifact, predict_home_win_proba

games = fetch_schedule_for_date("2025-04-06")
fr = build_features_for_game(games[0])
model, cols = load_artifact("artifacts/model.joblib")
import numpy as np
X = np.array([[fr.features[c] for c in cols]])
predict_home_win_proba(model, X)
```

## Feature columns

Order is fixed in `baseball_analyze.features.FEATURE_COLUMNS`:

- `diff_wrc_plus`, `diff_ops_vs_sp_hand`, `diff_team_fip`
- `diff_starter_fip`, `diff_starter_xfip`, `diff_starter_kbb9`
- `diff_starter_rest_days`, `diff_starter_recent_fip`
- `diff_bullpen_fip`, `park_factor_runs`, `home_field`

By default `build_features_for_game` freezes rates through the **day before** the
game (MLB as-of tables + pitcher game logs for rest / last-3-start FIP). Starter
FIP / xFIP / K-BB come from MLB Stats API; team offense/pitching/bullpen as-of
tables aggregate MLB `byDateRange` splits (Savant season tables remain available
when `use_as_of=False`). Park factors live in `data/park_data.py`.

### In-game features (Stage 5)

Live win-probability features are **not** the pregame set. Use `baseball_analyze.features.in_game`:

- Columns (`IN_GAME_FEATURE_COLUMNS`): score differential, inning, half-inning, outs, base occupancy, count, current pitcher FIP / K-BB/9, same-hand matchup, defending bullpen FIP, reliever flag
- `iter_pre_play_states` / `build_in_game_training_rows_from_feed` — historical play-by-play → training rows (label = final home win)
- `state_from_linescore` / `build_in_game_features_from_feed` — current linescore snapshot for live inference
- Train/eval: `baseball-analyze-train-in-game` (see above); register with `kind=in_game`

## Full local stack (Docker)

The local happy path is one Compose command. Dockerfiles live under `infra/` (`Dockerfile.api`, `Dockerfile.worker`, `Dockerfile.web`). Host ports come from `docker-compose.override.yml` (auto-merged locally).

```bash
cp .env.example .env   # optional; Compose has sensible local defaults
docker compose up --build
```

This starts **Postgres**, **Redis**, **API** (runs Alembic migrations on boot), **live worker**, **notification worker**, and **Next.js** web.

| Service | URL / notes |
|---------|-------------|
| Web | http://localhost:3000 |
| API | http://localhost:8000 (`GET /health`, `/ready`, `/metrics`) |
| Postgres | `localhost:5432` (`baseball` / `baseball`) |
| Redis | `localhost:6379` |

Host `./artifacts` and `./cache` are mounted into the API and live worker so trained models and FanGraphs caches persist. Override secrets and LLM/SMTP settings via `.env` (see `.env.example`). Rebuild the web image after changing `NEXT_PUBLIC_API_URL` (default `http://localhost:8000` for browser → host-mapped API).

Infra-only (Postgres + Redis) for local Python/Node development:

```bash
docker compose up -d db redis
```

## Oracle Always Free (production)

Full-stack hosting on a single Always Free VM is documented in [`infra/HOSTING.md`](infra/HOSTING.md): provision Ampere A1 (2–4 OCPU / 12–24 GB preferred), bootstrap Docker, set production `.env` (`COMPOSE_FILE`, `DOMAIN`, secrets), and run Compose behind **Caddy** (Let’s Encrypt on 80/443). Public layout is `https://$DOMAIN/` → web and `https://$DOMAIN/api/*` → API.

```bash
# On the VM after cloning
./infra/oracle-bootstrap.sh
# edit .env for production (COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml, …)
docker compose up -d --build
```

Cloudflare Tunnel (HTTPS without inbound 80/443) is covered in the same doc. CI deploy over SSH uses [`infra/deploy.sh`](infra/deploy.sh).
## Backend API (Stage 2)

FastAPI lives under `backend/`. The API depends on the editable `baseball-analyze` package.

**Install and run the API** (from repo root, with `db`/`redis` up):

```bash
pip install -e ".[backend]"
cp .env.example .env
baseball-api
```

Or with uvicorn directly:

```bash
uvicorn baseball_backend.main:app --reload
```

**Health / readiness:**
- `GET /health` — liveness
- `GET /ready` — Postgres + Redis (when enabled); includes worker heartbeat lag and cache counters
- `GET /metrics` — Prometheus metrics (API latency, 5xx rate, live cache hit/miss, worker lag)

JSON structured logs default on (`LOG_LEVEL`, `LOG_JSON`). Light load test: `python scripts/load_test.py --base-url http://127.0.0.1:8000`. See [`infra/HOSTING.md`](infra/HOSTING.md#observability-stage-74).

**Database migrations** (from `backend/`, with Postgres running; also run automatically by the API container):

```bash
cd backend
alembic upgrade head
```

**Schedule sync / backfill** (populate `games` / `teams` from MLB):

```bash
# Today (or a single day)
baseball-sync-schedule
baseball-sync-schedule --date 2025-09-08

# Full regular season (one MLB season fetch)
baseball-sync-schedule --season 2025

# Calendar range across seasons (fetches each overlapping season once, then filters)
baseball-sync-schedule --from 2024-09-01 --to 2025-09-01
```

In Docker: `docker compose exec api baseball-sync-schedule --season 2025`. Bulk modes skip follower alerts unless you pass `--alerts`. Regular season only by default (`--game-type R`).

Environment variables (see `.env.example`): `DATABASE_URL`, `SECRET_KEY`, `CORS_ORIGINS`, `API_HOST`, `API_PORT`, `ARTIFACTS_ROOT`, `REDIS_URL`, `REDIS_ENABLED`, `LIVE_CACHE_TTL_COMPLETED_SECONDS`, `LIVE_PUBSUB_ENABLED`, `LIVE_POLL_INTERVAL_SECONDS`, `LIVE_POLL_GAME_DELAY_SECONDS`, `LIVE_POLL_MIN_REQUEST_INTERVAL_SECONDS`, `LIVE_SYNC_RETRIES`, `LIVE_SYNC_BACKOFF_SECONDS`, `LIVE_STALE_AFTER_SECONDS`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`, `NOTIFICATION_POLL_INTERVAL_SECONDS`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` (also `OPENAI_*` fallbacks used by the chat REPL), `LOG_LEVEL`, `LOG_JSON`, `METRICS_ENABLED`.

The live worker (`baseball-live-worker`) writes current game state to Redis and Postgres (`games.live_state`) after each poll. Completed games expire from the cache after `LIVE_CACHE_TTL_COMPLETED_SECONDS` (default 1 hour). When `LIVE_PUBSUB_ENABLED` is true, updates are published on `live:game:{game_pk}:updates` for API/WebSocket fan-out.

**Notifications (Stage 6):** preferences and in-app inbox live under `/notifications`. Alert rules (Stage 6.3) run from the live worker, schedule sync, and prediction jobs — not on request handlers — and call `notification_service.enqueue_notification` for followers of the game’s teams (or relevant players). Rules cover game start, WP swings above the user’s threshold, high-leverage late innings with runners on, game final, and new pregame predictions. Dispatches are deduped in `alert_dispatches` so poll loops stay idempotent. Deliver email with:

```bash
baseball-notification-worker --once
# or continuous:
baseball-notification-worker
```

Configure SMTP (`SMTP_HOST`, `SMTP_FROM`, optional `SMTP_USER` / `SMTP_PASSWORD`) before enabling email delivery.

**Analytics (Stage 6.4):** authenticated team and player pages backed by stored games, play events, and FanGraphs disk caches:

- `GET /teams/{team_id}/analytics?season=` — record, home/away splits, monthly trend, model accuracy, FanGraphs season row
- `GET /players/{player_id}/analytics?season=` — probable-start outcomes, event splits, FanGraphs pitcher row (name match)
- `GET /analytics/matchup?home_team_id=&away_team_id=&season=` — head-to-head games + FanGraphs diffs

Frontend: `/analytics` hub, `/teams/[teamId]`, `/players/[playerId]`, `/analytics/matchup`.

**AI assist (Stage 6.5):** authenticated grounded endpoints reuse the same tool-calling contract as `baseball-analyze chat` (`run_grounded_chat` in `chat_repl.py`). Probabilities and stats come only from stored predictions / chat tools — never invented by the LLM.

- `POST /ai/explain` — `{ "game_pk" }` narrates the stored pregame lean from features + probs
- `POST /ai/summarize-game` — `{ "game_pk" }` summarizes schedule, score, WP, and recent events
- `POST /ai/ask` — `{ "question", "game_pk"?, "date"? }` full NL Q&A with schedule + `predict_games` tools

Install the chat extra (`pip install -e ".[chat]"`) and set `LLM_API_KEY` / `OPENAI_API_KEY` (or `LLM_BASE_URL` for a local OpenAI-compatible server). Misconfiguration returns `503`. Game detail UI (`/games/[gamePk]`) exposes Explain / Summarize / Ask.

**Live resilience:** play events are deduped by MLB `atBatIndex` (`play-{n}`) with DB uniqueness; MLB fetches retry with exponential backoff (and honor `Retry-After` on 429); all MLB Stats API calls share a process-wide min interval (`MLB_MIN_REQUEST_INTERVAL_SECONDS`) and live polls add another spacer; when Redis or the MLB feed is unavailable (or the snapshot is older than `LIVE_STALE_AFTER_SECONDS`), the API serves the last Postgres snapshot with `degraded=true` / `/health.live_degraded` so the UI shows a “Live data degraded” banner on the home slate and game page.

**Ops (Stage 7.5):** see [`infra/RUNBOOK.md`](infra/RUNBOOK.md) for degraded feed, worker lag, MLB 429, SMTP, Postgres restore, and deploy rollback. VM bootstrap applies host firewall + unattended updates (`infra/host-harden.sh`) and installs daily `infra/backup-postgres.sh` cron.

**Live WebSockets** (authenticated via `?token=` JWT query param):

- `WS /ws/games/{game_pk}` — initial scoreboard snapshot (Redis, or Postgres if cache miss), then live updates (score, inning, outs, count, status)
- `WS /ws/live?date=YYYY-MM-DD` — same for all games on a date slate (`slate_snapshot` then per-game `update` messages)

When Redis pub/sub is disabled but Redis caching is on, the API polls the cache and forwards changes to connected clients.

**Live HTTP (polling fallback for the game page):**

- `GET /games/{game_pk}/live` — same scoreboard snapshot as the WS handshake (`data`, `source`, `degraded`)
- `GET /games/{game_pk}/events` — play-by-play rows for the timeline

### Model registry (Stage 3 / Stage 5)

After training, register a versioned run in Postgres so the API can load the active model for inference:

```bash
# Pregame (default when manifest has no kind)
baseball-register-model 20260824T200812Z_deadbeef --activate

# In-game live WP (manifest from baseball-analyze-train-in-game already sets kind=in_game)
baseball-register-model 20260906T120000Z_ingame01 --activate
baseball-register-model 20260906T120000Z_ingame01 --kind in_game --activate
```

This reads `artifacts/<run_id>/{model.joblib,metrics.json,manifest.json}`, upserts a `model_versions` row (metrics, feature columns, hyperparameters, train seasons), and optionally sets `status=active` while archiving other active models of the **same kind** only. Pregame and in-game each keep their own active version.

## Frontend (Stage 2)

Next.js App Router app under `frontend/`. Authenticated users can register, sign in, browse today's MLB schedule, open game details, and view a profile shell.

**Setup:**

```bash
cd frontend
cp .env.local.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`. The dev server expects the API at `http://localhost:8000` (override with `NEXT_PUBLIC_API_URL` in `.env.local`).

**Pages:**

- `/login`, `/register` — JWT auth (token stored in `localStorage`)
- `/` — schedule dashboard with date picker
- `/games/[gamePk]` — game detail with live scoreboard (live WP + rule-based swing notes when WP moves ≥5pp), play-by-play timeline, WebSocket updates (falls back to HTTP polling), and pregame prediction
- `/model` — model performance
- `/profile` — account, watchlist, notification preferences, and in-app inbox

## Tests

```bash
pip install -e ".[dev,backend]"
pytest -q
```

Python critical lint (also run in CI):

```bash
ruff check src backend tests
```

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run build
```

## CI / CD (GitHub Actions)

Workflows live under `.github/workflows/`:

| Workflow | When | What |
|----------|------|------|
| **CI** (`ci.yml`) | Push / PR to `main` | Ruff + pytest (ML + backend), ESLint + Next build, Docker image builds for api / worker / web |
| **Deploy** (`deploy.yml`) | Manual (`workflow_dispatch`), or push to `main` when enabled | SSH to the Compose host, checkout the commit, `docker compose up --build -d`, run `alembic upgrade head`, wait for `/health` |

Deploy is **opt-in** so everyday pushes stay green before a VM exists. To enable automatic deploys on `main`:

1. Follow [`infra/HOSTING.md`](infra/HOSTING.md): provision the Oracle Always Free VM, bootstrap Docker, clone this repo (e.g. `~/baseball-chatbot`), and create a production `.env` with `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml`.
2. Add repository **secrets**: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` (private key). Optional: `DEPLOY_PORT` (default `22`), `DEPLOY_PATH` (default `~/baseball-chatbot`).
3. Set repository **variable** `ENABLE_DEPLOY` = `true`.
4. Or run **Actions → Deploy → Run workflow** without enabling auto-deploy.

The remote script is [`infra/deploy.sh`](infra/deploy.sh). The API container entrypoint also migrates on boot; the deploy job runs Alembic explicitly so schema updates apply even if the API image was already warm. Health is checked inside the API container (works when only Caddy publishes 80/443).

## Troubleshooting

- **`RuntimeError: No training rows collected`** — Training never built a single feature row, so **`artifacts/` is not created.** A common cause was **invalid FanGraphs stat names** in older `pybaseball` versions; this repo uses **numeric FanGraphs stat ids** for team batting/pitching. If you still see this after pulling updates, delete `./cache/` and retry.
- **Shell exits with code 1** — Check the full traceback; until training completes successfully, there will be no `artifacts/model.joblib`.
