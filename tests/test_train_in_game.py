"""Tests for in-game model training pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from baseball_analyze.data.mlb_client import ScheduledGame
from baseball_analyze.features.in_game import IN_GAME_FEATURE_COLUMNS
from baseball_analyze.models.model import load_artifact
from baseball_analyze.models.train_in_game import (
    IN_GAME_KIND,
    _run_training,
    build_in_game_training_sample,
)
from baseball_analyze.models.training_config import TrainingConfig, load_training_config

FIXTURE = Path(__file__).parent / "fixtures" / "live_feed_sample.json"


def _load_feed() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _fake_game(game_pk: int, *, home_won: bool = True) -> ScheduledGame:
    return ScheduledGame(
        game_pk=game_pk,
        game_date="2023-06-01",
        season=2023,
        status="Final",
        detailed_state="Final",
        home_team_id=145,
        away_team_id=116,
        home_abbrev="CWS",
        away_abbrev="DET",
        venue_id=1,
        home_probable_id=None,
        away_probable_id=None,
        home_score=3 if home_won else 2,
        away_score=2 if home_won else 4,
    )


def _fake_bullpen(_season: int, cache_dir=None) -> pd.Series:
    return pd.Series({"CWS": 3.90, "DET": 4.10})


def _fake_pitcher_fip(mlb_id: int, _season: int):
    return (3.50 if mlb_id % 2 == 0 else 4.00), 3.60


def _fake_pitcher_kbb9(mlb_id: int, _season: int):
    return 5.0 if mlb_id % 2 == 0 else 3.0


def _feed_for_game(game_pk: int, *, home_won: bool) -> dict:
    feed = _load_feed()
    teams = ((feed.get("liveData") or {}).get("linescore") or {}).get("teams") or {}
    if "home" in teams and "away" in teams:
        teams["home"]["runs"] = 5 if home_won else 2
        teams["away"]["runs"] = 2 if home_won else 5
    return feed


@patch("baseball_analyze.features.in_game.fetch_pitcher_season_kbb9", side_effect=_fake_pitcher_kbb9)
@patch("baseball_analyze.features.in_game.fetch_pitcher_season_fip_xfip", side_effect=_fake_pitcher_fip)
@patch("baseball_analyze.features.in_game.bullpen_fip_by_team", side_effect=_fake_bullpen)
@patch(
    "baseball_analyze.features.in_game.mlb_abbrev_to_fangraphs",
    side_effect=lambda abbr, _season: {"CWS": "CWS", "CHW": "CWS", "DET": "DET"}.get(abbr, abbr),
)
@patch("baseball_analyze.models.train_in_game.fetch_live_feed")
@patch("baseball_analyze.models.train_in_game.iter_completed_games")
def test_build_in_game_training_sample(
    mock_iter,
    mock_feed,
    _map,
    _bull,
    _fip,
    _kbb9,
) -> None:
    games = [_fake_game(1001, home_won=True), _fake_game(1002, home_won=False)]
    mock_iter.return_value = iter(games)
    mock_feed.side_effect = lambda pk: _feed_for_game(pk, home_won=(pk == 1001))

    X, y, rows = build_in_game_training_sample(
        [2023],
        cache_dir=None,
        max_games=2,
        sleep_s=0.0,
    )

    assert len(rows) > 0
    assert X.shape == (len(rows), len(IN_GAME_FEATURE_COLUMNS))
    assert y.shape == (len(rows),)
    assert set(y.tolist()) == {0, 1}
    assert all(r.label_home_win is not None for r in rows)


@patch("baseball_analyze.models.train_in_game.bullpen_fip_by_team", side_effect=_fake_bullpen)
@patch("baseball_analyze.features.in_game.fetch_pitcher_season_kbb9", side_effect=_fake_pitcher_kbb9)
@patch("baseball_analyze.features.in_game.fetch_pitcher_season_fip_xfip", side_effect=_fake_pitcher_fip)
@patch("baseball_analyze.features.in_game.bullpen_fip_by_team", side_effect=_fake_bullpen)
@patch(
    "baseball_analyze.features.in_game.mlb_abbrev_to_fangraphs",
    side_effect=lambda abbr, _season: {"CWS": "CWS", "CHW": "CWS", "DET": "DET"}.get(abbr, abbr),
)
@patch("baseball_analyze.models.train_in_game.fetch_live_feed")
@patch("baseball_analyze.models.train_in_game.iter_completed_games")
def test_run_in_game_training_writes_versioned_artifacts(
    mock_iter,
    mock_feed,
    _map,
    _bull_feat,
    _fip,
    _kbb9,
    _bull_train,
    tmp_path: Path,
) -> None:
    games = [
        _fake_game(2001, home_won=True),
        _fake_game(2002, home_won=False),
        _fake_game(2003, home_won=True),
        _fake_game(2004, home_won=False),
    ]
    mock_iter.return_value = iter(games)
    mock_feed.side_effect = lambda pk: _feed_for_game(pk, home_won=(pk % 2 == 1))

    out = tmp_path / "artifacts" / "in_game_model.joblib"
    log_csv = tmp_path / "artifacts" / "in_game_training_log.csv"
    cfg = TrainingConfig(
        seasons=[2023],
        val_seasons=[],
        out=out,
        log_csv=log_csv,
        test_size=0.5,
        max_games=4,
        hyperparameters={"calibrate": False, "class_weight": "balanced", "c_grid": [1.0]},
        random_state=42,
    )

    with patch("baseball_analyze.models.train_in_game.typer.echo"):
        _run_training(cfg)

    assert out.is_file()
    assert log_csv.is_file()

    run_dirs = [p for p in (tmp_path / "artifacts").iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))

    assert manifest["kind"] == IN_GAME_KIND
    assert manifest["feature_columns"] == IN_GAME_FEATURE_COLUMNS
    assert set(metrics) == {"accuracy", "roc_auc", "log_loss", "brier"}

    model, cols = load_artifact(out, expected_columns=IN_GAME_FEATURE_COLUMNS)
    assert isinstance(model, Pipeline)
    assert cols == IN_GAME_FEATURE_COLUMNS

    with pytest.raises(ValueError, match="Feature mismatch"):
        load_artifact(out)  # defaults to pregame columns


def test_load_in_game_training_config() -> None:
    cfg = load_training_config(Path("configs/in_game_logistic_regression.yaml"))
    assert cfg.out == Path("artifacts/in_game_model.joblib")
    assert cfg.log_csv == Path("artifacts/in_game_training_log.csv")
    assert cfg.seasons == [2023]
    assert cfg.hyperparameters.class_weight == "balanced"
