from unittest.mock import patch

from baseball_analyze.data.mlb_client import ScheduledGame


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
    with patch("baseball_analyze.chat_tools.predict_game") as pred:
        pred.side_effect = [
            {
                "game_pk": 1,
                "season": 2025,
                "away_fg": "BOS",
                "home_fg": "NYY",
                "home_win_proba": 0.42,
                "away_win_proba": 0.58,
                "features": {},
                "model_version": "run_v1",
                "notes": ["note1"],
            },
            ValueError("Could not load schedule for gamePk=2"),
        ]

        from baseball_analyze.chat_tools import predict_games

        out = predict_games(model_path="artifacts/model.joblib", game_pks=[1, 2], cache_dir=None)

    assert out[0]["game_pk"] == 1
    assert out[0]["p_home_win"] == 0.42
    assert out[0]["p_away_win"] == 0.58
    assert out[0]["away_fg"] == "BOS"
    assert out[0]["home_fg"] == "NYY"
    assert out[0]["notes"] == ["note1"]
    assert out[0]["model_version"] == "run_v1"
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
