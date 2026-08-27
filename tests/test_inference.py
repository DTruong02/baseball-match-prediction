from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from baseball_analyze.features import FEATURE_COLUMNS, FeatureRow
from baseball_analyze.mlb_client import ScheduledGame
from baseball_analyze.models.inference import predict_game, resolve_model_version


def _game(pk: int, state: str = "Scheduled") -> ScheduledGame:
    return ScheduledGame(
        game_pk=pk,
        game_date="2025-04-06",
        season=2025,
        status="Preview",
        detailed_state=state,
        home_team_id=147,
        away_team_id=111,
        home_abbrev="NYY",
        away_abbrev="BOS",
        venue_id=3313,
        home_probable_id=1,
        away_probable_id=2,
    )


def test_resolve_model_version_from_manifest(tmp_path: Path) -> None:
    run_dir = tmp_path / "20250101T000000Z_abcd1234"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        '{"run_id": "20250101T000000Z_abcd1234"}\n',
        encoding="utf-8",
    )
    model_path = run_dir / "model.joblib"
    model_path.write_bytes(b"x")

    assert resolve_model_version(model_path) == "20250101T000000Z_abcd1234"


def test_resolve_model_version_falls_back_to_path(tmp_path: Path) -> None:
    model_path = tmp_path / "model.joblib"
    model_path.write_bytes(b"x")
    assert resolve_model_version(model_path) == str(model_path.resolve())


def test_predict_game_mocked() -> None:
    fr = FeatureRow(
        game_pk=1,
        season=2025,
        home_fg="NYY",
        away_fg="BOS",
        features={c: 0.0 for c in FEATURE_COLUMNS},
        notes=["note1"],
    )
    fr.features["diff_wrc_plus"] = 1.0

    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.4, 0.6]])

    with patch("baseball_analyze.models.inference.fetch_schedule_by_game_pk") as fetch, patch(
        "baseball_analyze.models.inference.load_artifact"
    ) as load, patch(
        "baseball_analyze.models.inference.build_features_for_game"
    ) as build, patch(
        "baseball_analyze.models.inference.resolve_model_version"
    ) as version:
        fetch.return_value = _game(1)
        load.return_value = (model, FEATURE_COLUMNS)
        build.return_value = fr
        version.return_value = "run_v1"

        out = predict_game(1, "artifacts/model.joblib", cache_dir=None)

    assert out["game_pk"] == 1
    assert out["home_win_proba"] == 0.6
    assert out["away_win_proba"] == 0.4
    assert out["features"]["diff_wrc_plus"] == 1.0
    assert out["model_version"] == "run_v1"
    assert out["notes"] == ["note1"]
    assert out["away_fg"] == "BOS"
    assert out["home_fg"] == "NYY"


def test_predict_game_raises_when_missing() -> None:
    with patch("baseball_analyze.models.inference.fetch_schedule_by_game_pk", return_value=None):
        with pytest.raises(ValueError, match="Could not load schedule"):
            predict_game(99, "artifacts/model.joblib")


def test_predict_game_raises_when_postponed() -> None:
    with patch(
        "baseball_analyze.models.inference.fetch_schedule_by_game_pk",
        return_value=_game(1, "Postponed"),
    ):
        with pytest.raises(ValueError, match="Postponed"):
            predict_game(1, "artifacts/model.joblib")
