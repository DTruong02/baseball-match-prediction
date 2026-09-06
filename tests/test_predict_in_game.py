"""Tests for in-game (live WP) inference."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from baseball_analyze.features.in_game import IN_GAME_FEATURE_COLUMNS, InGameFeatureRow
from baseball_analyze.models.inference import predict_in_game

LIVE_FEED = json.loads(
    (Path(__file__).parent / "fixtures" / "live_feed_sample.json").read_text(
        encoding="utf-8"
    )
)


def test_predict_in_game_mocked() -> None:
    fr = InGameFeatureRow(
        game_pk=824239,
        season=2026,
        at_bat_index=7,
        features={c: 0.0 for c in IN_GAME_FEATURE_COLUMNS},
        notes=["note"],
    )
    fr.features["score_diff"] = -1.0

    model = MagicMock()
    model.predict_proba.return_value = np.array([[0.35, 0.65]])

    with (
        patch("baseball_analyze.models.inference.build_in_game_features_from_feed") as build,
        patch("baseball_analyze.models.inference.load_artifact") as load,
        patch("baseball_analyze.models.inference.resolve_model_version") as version,
    ):
        build.return_value = fr
        load.return_value = (model, IN_GAME_FEATURE_COLUMNS)
        version.return_value = "in_game_run"

        out = predict_in_game(
            LIVE_FEED,
            "artifacts/in_game_model.joblib",
            game_pk=824239,
            season=2026,
            home_abbrev="NYY",
            away_abbrev="BOS",
        )

    assert out["game_pk"] == 824239
    assert out["home_win_proba"] == pytest.approx(0.65)
    assert out["away_win_proba"] == pytest.approx(0.35)
    assert out["features"]["score_diff"] == -1.0
    assert out["model_version"] == "in_game_run"
    assert out["notes"] == ["note"]


def test_predict_in_game_raises_when_state_missing() -> None:
    with patch(
        "baseball_analyze.models.inference.build_in_game_features_from_feed",
        return_value=None,
    ):
        with pytest.raises(ValueError, match="Could not build in-game features"):
            predict_in_game(
                {},
                "artifacts/in_game_model.joblib",
                game_pk=1,
                season=2026,
                home_abbrev="NYY",
                away_abbrev="BOS",
            )
