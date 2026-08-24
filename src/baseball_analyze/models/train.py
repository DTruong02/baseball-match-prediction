"""Train a home-win model from completed MLB seasons (v1 simplification)."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Iterator, List, Optional, Tuple
import csv
import datetime as dt

import httpx
import numpy as np
import typer
from sklearn.model_selection import train_test_split

from baseball_analyze.data.fangraphs_features import (
    bullpen_fip_by_team,
    load_team_batting,
    load_team_pitching,
    median_starter_fip_by_team,
)
from baseball_analyze.data.mlb_client import (
    MLBAPIError,
    ScheduledGame,
    extract_starting_pitcher_ids,
    fetch_boxscore,
    fetch_linescore,
    fetch_schedule_season,
    home_team_won_from_linescore,
)
from baseball_analyze.features import (
    FEATURE_COLUMNS,
    FeatureRow,
    build_features_for_game,
    features_dict_to_matrix,
)
from baseball_analyze.models.model import evaluate, predict_home_win_proba, save_artifact, train_pipeline


def _append_training_log_csv(
    log_path: Path,
    row: dict[str, object],
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    exists = log_path.exists()
    # stable column order (keep simple, mostly scalar fields)
    fieldnames = [
        "timestamp_utc",
        "model_out",
        "seasons",
        "val_seasons",
        "split_type",
        "train_rows",
        "val_rows",
        "max_games",
        "calibrate",
        "class_weight",
        "c_grid",
        "best_C",
        "brier",
        "log_loss",
        "accuracy",
        "features",
    ]
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fieldnames})


def iter_completed_games(season: int) -> Iterator[ScheduledGame]:
    yield from fetch_schedule_season(season)


def build_training_sample(
    seasons: List[int],
    cache_dir: Optional[Path],
    max_games: Optional[int],
    sleep_s: float = 0.12,
) -> Tuple[np.ndarray, np.ndarray, List[FeatureRow]]:
    """
    v1 training simplification:
    Team/bullpen stats use full **season** FanGraphs tables (see README caveat).
    Starters are taken from the **box score** (known after the game), not historical probables.
    """
    rows: list[FeatureRow] = []
    labels: list[int] = []
    n_done = 0
    scan_count = 0
    max_scans = (max_games * 400) if max_games is not None else None
    last_progress = time.time()
    progress_every_scans = 250
    progress_every_seconds = 15.0

    for season in seasons:
        for g in iter_completed_games(season):
            scan_count += 1
            now = time.time()
            if (
                scan_count % progress_every_scans == 0
                or (now - last_progress) >= progress_every_seconds
            ):
                typer.echo(
                    f"[progress] scanned={scan_count} successes={n_done}"
                    + (f" target={max_games}" if max_games is not None else "")
                )
                last_progress = now
            if max_games is not None and n_done >= max_games:
                break
            if max_scans is not None and scan_count > max_scans:
                raise RuntimeError(
                    f"Stopped after {scan_count} schedule rows without enough successes; "
                    "try a different season or inspect network/pybaseball errors."
                )
            if g.detailed_state not in ("Final", "Completed Early"):
                continue
            time.sleep(sleep_s)
            try:
                ls = fetch_linescore(g.game_pk)
                yb = home_team_won_from_linescore(ls)
                if yb is None:
                    continue
                box = fetch_boxscore(g.game_pk)
                hs, aw = extract_starting_pitcher_ids(box)
            except (MLBAPIError, KeyError, httpx.HTTPError):
                continue

            if hs is None or aw is None:
                continue

            g2 = replace(
                g,
                home_probable_id=hs,
                away_probable_id=aw,
            )
            try:
                fr = build_features_for_game(g2, cache_dir=cache_dir)
            except Exception:
                continue

            rows.append(fr)
            labels.append(1 if yb else 0)
            n_done += 1

        if max_games is not None and n_done >= max_games:
            break

    if not rows:
        raise RuntimeError("No training rows collected; check seasons and network.")

    X = features_dict_to_matrix(rows)
    y = np.array(labels, dtype=int)
    return X, y, rows


def train_run(
    seasons: str = typer.Option(
        "2022,2023",
        help="Comma-separated seasons for training+validation pool (e.g. 2022,2023,2024).",
    ),
    val_seasons: str = typer.Option(
        "",
        help="Comma-separated seasons to use as validation (time split). Example: --seasons 2024,2025 --val-seasons 2025",
    ),
    out: Path = typer.Option(
        Path("artifacts/model.joblib"),
        help="Where to save the sklearn pipeline.",
    ),
    test_size: float = typer.Option(0.25, help="Holdout fraction for metrics (time-agnostic split)."),
    max_games: Optional[int] = typer.Option(
        None,
        help="Cap rows for a quick smoke test (default: all Final games).",
    ),
    log_csv: Path = typer.Option(
        Path("artifacts/training_log.csv"),
        help="Append one row per training run to this CSV file.",
    ),
    tune_c: str = typer.Option(
        "",
        help="Comma-separated C values to grid search (e.g. 0.05,0.1,0.5,1,5,10). Chooses best by validation log_loss.",
    ),
    class_weight: str = typer.Option(
        "balanced",
        help="LogisticRegression class_weight: 'balanced' or 'none'.",
    ),
    calibrate: bool = typer.Option(False, help="Use isotonic calibration (slower, needs enough rows)."),
    cache_dir: Optional[Path] = typer.Option(None, help="Override cache directory for FanGraphs tables."),
    random_state: int = typer.Option(42, help="Random seed for the train/val split."),
) -> None:
    """Collect games, train logistic regression, print metrics, save artifact."""
    season_list = [int(s.strip()) for s in seasons.split(",") if s.strip()]
    val_list = [int(s.strip()) for s in val_seasons.split(",") if s.strip()]
    for s in season_list:
        typer.echo(f"Loading FanGraphs season tables for {s} (cached under ./cache/)...")
        load_team_batting(s, cache_dir=cache_dir)
        load_team_pitching(s, cache_dir=cache_dir)
        bullpen_fip_by_team(s, cache_dir=cache_dir)
        median_starter_fip_by_team(s, cache_dir=cache_dir)
    X, y, rows = build_training_sample(
        season_list,
        cache_dir=cache_dir,
        max_games=max_games,
    )
    # Split: time-based by season if val_seasons provided, else random split
    split_type = "random"
    if val_list:
        val_set = set(val_list)
        val_mask = np.array([r.season in val_set for r in rows], dtype=bool)
        if not val_mask.any():
            raise RuntimeError(f"--val-seasons {val_list} produced 0 validation rows. Check --seasons.")
        if val_mask.all():
            raise RuntimeError(f"--val-seasons {val_list} captured all rows; no training rows left.")
        X_train, y_train = X[~val_mask], y[~val_mask]
        X_val, y_val = X[val_mask], y[val_mask]
        typer.echo(f"Time split: train_rows={len(y_train)} val_rows={len(y_val)} val_seasons={sorted(val_set)}")
        split_type = "time"
    else:
        try:
            X_train, X_val, y_train, y_val = train_test_split(
                X, y, test_size=test_size, random_state=random_state, stratify=y
            )
        except ValueError:
            X_train, X_val, y_train, y_val = train_test_split(
                X, y, test_size=test_size, random_state=random_state
            )

    cw = None if class_weight.strip().lower() in ("none", "null", "0") else "balanced"

    # Regularization tuning (grid over C), pick best by validation log_loss
    c_grid = [1.0]
    if tune_c.strip():
        c_grid = [float(x.strip()) for x in tune_c.split(",") if x.strip()]
    best = None
    best_model = None
    best_C = None
    for C in c_grid:
        model = train_pipeline(X_train, y_train, calibrate=calibrate, C=C, class_weight=cw)
        proba = predict_home_win_proba(model, X_val)
        metrics = evaluate(y_val, proba)
        typer.echo(f"C={C:g} metrics={metrics}")
        if best is None or metrics["log_loss"] < best["log_loss"]:
            best = metrics
            best_model = model
            best_C = C

    assert best_model is not None and best is not None
    typer.echo(f"Best metrics: {best}")
    save_artifact(best_model, out)
    typer.echo(f"Saved model to {out} with features {FEATURE_COLUMNS}")

    _append_training_log_csv(
        log_csv,
        {
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "model_out": str(out),
            "seasons": ",".join(str(s) for s in season_list),
            "val_seasons": ",".join(str(s) for s in val_list),
            "split_type": split_type,
            "train_rows": int(len(y_train)),
            "val_rows": int(len(y_val)),
            "max_games": "" if max_games is None else int(max_games),
            "calibrate": bool(calibrate),
            "class_weight": "none" if cw is None else "balanced",
            "c_grid": ",".join(f"{c:g}" for c in c_grid),
            "best_C": "" if best_C is None else float(best_C),
            "brier": float(best["brier"]),
            "log_loss": float(best["log_loss"]),
            "accuracy": float(best["accuracy"]),
            #"features": ",".join(FEATURE_COLUMNS),
        },
    )
    typer.echo(f"Appended training log row to {log_csv}")


def main_train() -> None:
    typer.run(train_run)


if __name__ == "__main__":
    main_train()
