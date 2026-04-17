# baseball-chatbot — Pregame win probability (Python)

Installable package **`baseball-analyze`** that builds pregame features from the [MLB Stats API](https://statsapi.mlb.com/) and FanGraphs data via [`pybaseball`](https://github.com/jldbc/pybaseball), then predicts **P(home team wins)** with scikit-learn.

## Install

```bash
cd baseball-chatbot
pip install -e ".[dev]"
```

## Train a model

Training downloads **one season schedule** from MLB, then **two HTTP calls per game** (linescore + box score) plus FanGraphs tables (cached under `./cache/`). See more options in --help

```bash
baseball-analyze-train --seasons 2023 --max-games 300 --out artifacts/model.joblib
```

**v1 training caveat (from the plan):** team and bullpen stats use **full-season FanGraphs tables** for that year (not true “stats as of game day”). Starting pitchers are taken from the **box score** (known after the game), not from historical probables. This is enough to sanity-check the pipeline; tighter backtests need daily snapshots or pitch-by-pitch reconstruction.

## Predict (pregame)

Requires a trained `artifacts/model.joblib` (path configurable).

```bash
baseball-analyze predict --date 2025-04-06 --model artifacts/model.joblib
baseball-analyze predict --game-pk 778285 --explain
```

## Chat (LLM-backed, grounded)

The chat REPL uses an LLM to understand your question, then calls local tools that fetch schedules and run your trained sklearn model. It will not invent probabilities; it only prints numbers produced by `artifacts/model.joblib`.

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
The configuration can also be modified through CLI flags. See --help for more

## Library usage

```python
from baseball_analyze.mlb_client import fetch_schedule_for_date
from baseball_analyze.features import build_features_for_game
from baseball_analyze.model import load_artifact, predict_home_win_proba

games = fetch_schedule_for_date("2025-04-06")
fr = build_features_for_game(games[0])
model, _ = load_artifact("artifacts/model.joblib")
import numpy as np
X = np.array([[fr.features[c] for c in ["diff_wrc_plus", "diff_team_fip", "diff_starter_fip", "diff_bullpen_fip", "park_factor_runs", "home_field"]]])
predict_home_win_proba(model, X)
```

## Feature columns

Order is fixed in `baseball_analyze.features.FEATURE_COLUMNS`:

- `diff_wrc_plus`, `diff_team_fip`, `diff_starter_fip`, `diff_bullpen_fip`, `park_factor_runs`, `home_field`

**Starter FIP** comes from MLB Stats API `sabermetrics` pitching (no Chadwick / FanGraphs player id map). Team offense/defense and bullpen aggregates use FanGraphs via `pybaseball` (cached under `./cache/`).

Park factors are static defaults in `park_data.py`; refresh from FanGraphs if you need current-year precision.

## Tests

```bash
pytest -q
```

## Troubleshooting

- **`RuntimeError: No training rows collected`** — Training never built a single feature row, so **`artifacts/` is not created.** A common cause was **invalid FanGraphs stat names** in older `pybaseball` versions; this repo uses **numeric FanGraphs stat ids** for team batting/pitching. If you still see this after pulling updates, delete `./cache/` and retry.
- **Shell exits with code 1** — Check the full traceback; until training completes successfully, there will be no `artifacts/model.joblib`.
