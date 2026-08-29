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
| `features/` | Pregame feature engineering (`FEATURE_COLUMNS`, `build_features_for_game`) |
| `models/` | Training, sklearn artifact I/O, `predict_core`, **`inference.predict_game`** |
| `configs/` (repo root) | YAML training configs |
| `cli.py`, `chat_tools.py`, `chat_repl.py` | CLI and grounded chat REPL |

Thin re-exports at legacy paths (e.g. `baseball_analyze.mlb_client`) remain for compatibility; prefer the paths above.

## Train a model

Training downloads **one season schedule** from MLB, then **two HTTP calls per game** (linescore + box score) plus FanGraphs tables (cached under `./cache/`). See `--help` for all flags.

**Config-driven training** (CLI flags override YAML values):

```bash
baseball-analyze-train --config configs/logistic_regression.yaml
baseball-analyze-train --config configs/logistic_regression.yaml --max-games 400 --seasons 2023
```

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

### v1 training caveats (known leakage)

These limits are intentional for v1 pipeline sanity checks; tighter backtests need daily snapshots or play-by-play reconstruction.

1. **Full-season FanGraphs tables** — Team offense (`wRC+`), team pitching (`FIP`), and bullpen aggregates use the **entire season’s** FanGraphs table for that year, not stats strictly “as of” each game date. Early-season games therefore see end-of-season team strength.
2. **Box-score starting pitchers** — Training labels use starters from the **post-game box score**, not pregame probables. Inference uses scheduled probables from the MLB schedule API. Train and predict are aligned on *features* for live use, but historical training rows embed post-game pitcher identity.
3. **Park factors** — Static defaults in `data/park_data.py`; refresh from FanGraphs if you need current-year park precision.

Do not treat holdout metrics from this trainer as unbiased pregame forecasting benchmarks without addressing the above.

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

- `diff_wrc_plus`, `diff_ops_vs_sp_hand`, `diff_team_fip`, `diff_starter_fip`, `diff_starter_kbb9`, `diff_bullpen_fip`, `park_factor_runs`, `home_field`

**Starter FIP / K-BB** come from MLB Stats API `sabermetrics` pitching (no Chadwick / FanGraphs player id map). Team offense/defense and bullpen aggregates use FanGraphs via `pybaseball` (cached under `./cache/`).

Park factors are static defaults in `data/park_data.py`; refresh from FanGraphs if you need current-year precision.

## Backend API (Stage 2)

Local Postgres and a FastAPI skeleton live under `backend/`. The API depends on the editable `baseball-analyze` package.

**Start Postgres:**

```bash
docker compose up -d db
```

**Install and run the API** (from repo root):

```bash
pip install -e ".[backend]"
cp .env.example .env
baseball-api
```

Or with uvicorn directly:

```bash
uvicorn baseball_backend.main:app --reload
```

**Health check:** `GET http://localhost:8000/health`

**Database migrations** (from `backend/`, with Postgres running):

```bash
cd backend
alembic upgrade head
```

Environment variables (see `.env.example`): `DATABASE_URL`, `SECRET_KEY`, `API_HOST`, `API_PORT`, `ARTIFACTS_ROOT`.

### Model registry (Stage 3)

After training, register a versioned run in Postgres so the API can load the active pregame model for inference:

```bash
baseball-register-model 20260824T200812Z_deadbeef --activate
```

This reads `artifacts/<run_id>/{model.joblib,metrics.json,manifest.json}`, upserts a `model_versions` row (metrics, feature columns, hyperparameters, train seasons), and optionally sets `status=active` while archiving other active models of the same kind.

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
- `/games/[gamePk]` — game detail (predictions stub until Stage 3)
- `/profile` — account shell

## Tests

```bash
pip install -e ".[dev,backend]"
pytest -q
```

## Troubleshooting

- **`RuntimeError: No training rows collected`** — Training never built a single feature row, so **`artifacts/` is not created.** A common cause was **invalid FanGraphs stat names** in older `pybaseball` versions; this repo uses **numeric FanGraphs stat ids** for team batting/pitching. If you still see this after pulling updates, delete `./cache/` and retry.
- **Shell exits with code 1** — Check the full traceback; until training completes successfully, there will be no `artifacts/model.joblib`.
