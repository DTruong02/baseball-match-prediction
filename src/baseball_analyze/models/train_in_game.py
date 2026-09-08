"""Train an in-game (live WP) home-win model from historical play-by-play."""

from __future__ import annotations

import csv
import datetime as dt
import time
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import httpx
import numpy as np
import typer
from sklearn.model_selection import train_test_split

from baseball_analyze.data.fangraphs_features import bullpen_fip_by_team
from baseball_analyze.data.mlb_client import (
    MLBAPIError,
    ScheduledGame,
    fetch_live_feed,
    fetch_schedule_season,
    home_team_won_from_linescore,
)
from baseball_analyze.features.in_game import (
    IN_GAME_FEATURE_COLUMNS,
    InGameFeatureRow,
    build_in_game_training_rows_from_feed,
    in_game_features_to_matrix,
)
from baseball_analyze.models.artifacts import (
    build_manifest,
    make_run_id,
    optional_git_hash,
    save_versioned_run,
)
from baseball_analyze.models.model import evaluate, predict_home_win_proba, train_pipeline
from baseball_analyze.models.training_config import (
    TrainingConfig,
    apply_cli_overrides,
    load_training_config,
)

IN_GAME_KIND = "in_game"


def _append_training_log_csv(
    log_path: Path,
    row: dict[str, object],
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    exists = log_path.exists()
    fieldnames = [
        "timestamp_utc",
        "run_id",
        "run_dir",
        "model_out",
        "kind",
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
        "roc_auc",
        "features",
    ]
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fieldnames})


def iter_completed_games(season: int) -> Iterator[ScheduledGame]:
    yield from fetch_schedule_season(season)


def build_in_game_training_sample(
    seasons: List[int],
    cache_dir: Optional[Path],
    max_games: Optional[int],
    sleep_s: float = 0.12,
) -> Tuple[np.ndarray, np.ndarray, List[InGameFeatureRow]]:
    """
    Build PA-level training rows from completed games' live/play-by-play feeds.

    Labels are the game's final home-win outcome (standard in-game WP training).
    ``max_games`` caps the number of *games*, not feature rows.
    """
    rows: list[InGameFeatureRow] = []
    n_games = 0
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
                    f"[progress] scanned={scan_count} games={n_games} rows={len(rows)}"
                    + (f" target_games={max_games}" if max_games is not None else "")
                )
                last_progress = now
            if max_games is not None and n_games >= max_games:
                break
            if max_scans is not None and scan_count > max_scans:
                raise RuntimeError(
                    f"Stopped after {scan_count} schedule rows without enough successes; "
                    "try a different season or inspect network/MLB API errors."
                )
            if g.detailed_state not in ("Final", "Completed Early"):
                continue

            time.sleep(sleep_s)
            try:
                feed = fetch_live_feed(g.game_pk)
                linescore = (feed.get("liveData") or {}).get("linescore") or {}
                home_won = home_team_won_from_linescore(linescore)
                if home_won is None:
                    continue
                game_rows = build_in_game_training_rows_from_feed(
                    feed,
                    game_pk=g.game_pk,
                    season=g.season,
                    home_abbrev=g.home_abbrev,
                    away_abbrev=g.away_abbrev,
                    home_won=home_won,
                    cache_dir=cache_dir,
                )
            except (MLBAPIError, KeyError, httpx.HTTPError, ValueError, TypeError):
                continue

            if not game_rows:
                continue

            rows.extend(game_rows)
            n_games += 1

        if max_games is not None and n_games >= max_games:
            break

    if not rows:
        raise RuntimeError("No in-game training rows collected; check seasons and network.")

    X = in_game_features_to_matrix(rows)
    y = np.array([1 if r.label_home_win else 0 for r in rows], dtype=int)
    return X, y, rows


def _game_level_masks(
    rows: list[InGameFeatureRow],
    *,
    test_size: float,
    random_state: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Split by game_pk so PAs from the same game stay in one fold."""
    game_pks = sorted({r.game_pk for r in rows})
    if len(game_pks) < 2:
        raise RuntimeError("Need at least 2 games to build a train/validation split.")

    labels_by_game = {
        pk: next(1 if r.label_home_win else 0 for r in rows if r.game_pk == pk)
        for pk in game_pks
    }
    y_games = np.array([labels_by_game[pk] for pk in game_pks], dtype=int)
    try:
        train_pks, val_pks = train_test_split(
            game_pks,
            test_size=test_size,
            random_state=random_state,
            stratify=y_games,
        )
    except ValueError:
        train_pks, val_pks = train_test_split(
            game_pks,
            test_size=test_size,
            random_state=random_state,
        )

    val_set = set(val_pks)
    val_mask = np.array([r.game_pk in val_set for r in rows], dtype=bool)
    return ~val_mask, val_mask


def _run_training(cfg: TrainingConfig) -> None:
    season_list = cfg.seasons
    val_list = cfg.val_seasons
    out = cfg.out
    test_size = cfg.test_size
    max_games = cfg.max_games
    log_csv = cfg.log_csv
    calibrate = cfg.hyperparameters.calibrate
    cw = cfg.hyperparameters.class_weight_sklearn
    c_grid = cfg.hyperparameters.c_grid
    cache_dir = cfg.cache_dir
    random_state = cfg.random_state

    for s in season_list:
        typer.echo(f"Loading Savant bullpen table for {s} (cached under ./cache/)...")
        bullpen_fip_by_team(s, cache_dir=cache_dir)

    X, y, rows = build_in_game_training_sample(
        season_list,
        cache_dir=cache_dir,
        max_games=max_games,
    )

    split_type = "random_by_game"
    if val_list:
        val_set = set(val_list)
        val_mask = np.array([r.season in val_set for r in rows], dtype=bool)
        if not val_mask.any():
            raise RuntimeError(f"val_seasons {val_list} produced 0 validation rows. Check seasons.")
        if val_mask.all():
            raise RuntimeError(f"val_seasons {val_list} captured all rows; no training rows left.")
        train_mask = ~val_mask
        split_type = "time"
    else:
        train_mask, val_mask = _game_level_masks(
            rows, test_size=test_size, random_state=random_state
        )

    X_train, y_train = X[train_mask], y[train_mask]
    X_val, y_val = X[val_mask], y[val_mask]
    typer.echo(
        f"Split ({split_type}): train_rows={len(y_train)} val_rows={len(y_val)} "
        f"games_train={len({r.game_pk for r, m in zip(rows, train_mask) if m})} "
        f"games_val={len({r.game_pk for r, m in zip(rows, val_mask) if m})}"
    )

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

    run_id = make_run_id()
    created_at = dt.datetime.now(dt.timezone.utc)
    hyperparams = {
        "calibrate": bool(calibrate),
        "class_weight": "none" if cw is None else "balanced",
        "c_grid": [float(c) for c in c_grid],
        "best_C": None if best_C is None else float(best_C),
    }
    git_hash = optional_git_hash()
    manifest = build_manifest(
        run_id=run_id,
        seasons=season_list,
        val_seasons=val_list,
        split_type=split_type,
        train_rows=int(len(y_train)),
        val_rows=int(len(y_val)),
        max_games=max_games,
        test_size=float(test_size),
        hyperparameters=hyperparams,
        feature_columns=IN_GAME_FEATURE_COLUMNS,
        created_at=created_at,
        git_hash=git_hash,
        kind=IN_GAME_KIND,
    )
    artifacts_root = out.parent
    run_dir = save_versioned_run(
        model=best_model,
        metrics=best,
        manifest=manifest,
        artifacts_root=artifacts_root,
        convenience_out=out,
        run_id=run_id,
    )
    typer.echo(f"Saved versioned run to {run_dir}")
    typer.echo(f"Copied convenience model to {out} with features {IN_GAME_FEATURE_COLUMNS}")

    _append_training_log_csv(
        log_csv,
        {
            "timestamp_utc": created_at.isoformat(timespec="seconds"),
            "run_id": run_id,
            "run_dir": str(run_dir),
            "model_out": str(out),
            "kind": IN_GAME_KIND,
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
            "roc_auc": float(best["roc_auc"]),
            "features": ",".join(IN_GAME_FEATURE_COLUMNS),
        },
    )
    typer.echo(f"Appended training log row to {log_csv}")


def train_in_game_run(
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        help="YAML training config path. CLI flags override config values.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    seasons: Optional[str] = typer.Option(
        None,
        help="Comma-separated seasons for training+validation pool (e.g. 2023,2024).",
    ),
    val_seasons: Optional[str] = typer.Option(
        None,
        help="Comma-separated seasons to use as validation (time split).",
    ),
    out: Optional[Path] = typer.Option(
        None,
        help="Where to save the sklearn pipeline convenience copy.",
    ),
    test_size: Optional[float] = typer.Option(
        None,
        help="Holdout fraction of games for metrics (when val_seasons is empty).",
    ),
    max_games: Optional[int] = typer.Option(
        None,
        help="Cap number of completed games for a quick smoke test.",
    ),
    log_csv: Optional[Path] = typer.Option(
        None,
        help="Append one row per training run to this CSV file.",
    ),
    tune_c: Optional[str] = typer.Option(
        None,
        help="Comma-separated C values to grid search. Chooses best by validation log_loss.",
    ),
    class_weight: Optional[str] = typer.Option(
        None,
        help="LogisticRegression class_weight: 'balanced' or 'none'.",
    ),
    calibrate: Optional[bool] = typer.Option(
        None, help="Use isotonic calibration (slower, needs enough rows)."
    ),
    cache_dir: Optional[Path] = typer.Option(
        None, help="Override cache directory for FanGraphs tables."
    ),
    random_state: Optional[int] = typer.Option(
        None, help="Random seed for the game-level train/val split."
    ),
) -> None:
    """Collect play-by-play states, train logistic regression, save versioned artifact."""
    base = (
        load_training_config(config)
        if config is not None
        else TrainingConfig(
            out=Path("artifacts/in_game_model.joblib"),
            log_csv=Path("artifacts/in_game_training_log.csv"),
        )
    )
    cfg = apply_cli_overrides(
        base,
        seasons=seasons,
        val_seasons=val_seasons,
        out=out,
        test_size=test_size,
        max_games=max_games,
        log_csv=log_csv,
        tune_c=tune_c,
        class_weight=class_weight,
        calibrate=calibrate,
        cache_dir=cache_dir,
        random_state=random_state,
    )
    if config is not None:
        typer.echo(f"Loaded training config from {config}")
    _run_training(cfg)


def main_train_in_game() -> None:
    typer.run(train_in_game_run)


if __name__ == "__main__":
    main_train_in_game()
