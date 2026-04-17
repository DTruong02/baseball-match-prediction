from unittest.mock import patch

import numpy as np

from baseball_analyze.features import FeatureRow
from baseball_analyze.mlb_client import ScheduledGame


def _game(pk: int, away: str = "BOS", home: str = "NYY") -> ScheduledGame:
    return ScheduledGame(
        game_pk=pk,
        game_date="2025-04-06",
        season=2025,
        status="Preview",
        detailed_state="Scheduled",
        home_team_id=147,
        away_team_id=111,
        home_abbrev=home,
        away_abbrev=away,
        venue_id=3313,
        home_probable_id=1,
        away_probable_id=2,
    )


def test_predict_games_mocked() -> None:
    fr = FeatureRow(
        game_pk=1,
        season=2025,
        home_fg="NYY",
        away_fg="BOS",
        features={"diff_wrc_plus": 1.0},
        notes=["note1"],
    )

    with patch("baseball_analyze.chat_tools.fetch_schedule_by_game_pk") as f_by_pk, patch(
        "baseball_analyze.chat_tools.build_feature_rows"
    ) as build_rows, patch("baseball_analyze.chat_tools.predict_for_feature_rows") as pred:
        f_by_pk.side_effect = [_game(1), None]
        build_rows.return_value = ([fr], [(1, ["note1"])])
        pred.return_value = np.array([0.42])

        from baseball_analyze.chat_tools import predict_games

        out = predict_games(model_path="artifacts/model.joblib", game_pks=[1, 2], cache_dir=None)

    assert out[0]["game_pk"] == 1
    assert out[0]["p_home_win"] == 0.42
    assert out[0]["away_fg"] == "BOS"
    assert out[0]["home_fg"] == "NYY"
    assert out[0]["notes"] == ["note1"]
    assert out[1]["warning"] == "missing_game_pks"
    assert out[1]["missing_game_pks"] == [2]


def test_find_games_on_date_maps_team_name() -> None:
    with patch("baseball_analyze.chat_tools.fetch_schedule_for_date") as f_sched, patch(
        "baseball_analyze.chat_tools.fetch_teams"
    ) as f_teams:
        f_sched.return_value = [_game(10, away="BOS", home="NYY"), _game(11, away="LAD", home="NYM")]
        f_teams.return_value = [
            {"abbreviation": "NYY", "name": "New York Yankees", "teamName": "Yankees", "clubName": "Yankees"},
            {"abbreviation": "BOS", "name": "Boston Red Sox", "teamName": "Red Sox", "clubName": "Red Sox"},
        ]

        from baseball_analyze.chat_tools import find_games_on_date

        hits = find_games_on_date(date="2025-04-06", away_team="Red Sox", home_team="Yankees")

    assert len(hits) == 1
    assert hits[0]["game_pk"] == 10

