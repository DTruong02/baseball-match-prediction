"""Tests for as-of / probable-aligned training helpers."""

from __future__ import annotations

from unittest.mock import patch

from baseball_analyze.data.mlb_client import ScheduledGame
from baseball_analyze.features import FEATURE_COLUMNS, FeatureRow
from baseball_analyze.models.train import build_training_sample


def _game(**kwargs) -> ScheduledGame:
    base = dict(
        game_pk=1,
        game_date="2024-06-15",
        season=2024,
        status="Final",
        detailed_state="Final",
        home_team_id=147,
        away_team_id=111,
        home_abbrev="NYY",
        away_abbrev="BOS",
        venue_id=3313,
        home_probable_id=10,
        away_probable_id=20,
    )
    base.update(kwargs)
    return ScheduledGame(**base)


def test_build_training_sample_prefers_probable_starters():
    games = [
        _game(game_pk=101, home_probable_id=10, away_probable_id=20),
        _game(game_pk=102, home_probable_id=None, away_probable_id=None),
    ]

    def fake_features(game, cache_dir=None, **kwargs):
        return FeatureRow(
            game_pk=game.game_pk,
            season=game.season,
            home_fg="NYY",
            away_fg="BOS",
            features={c: 0.0 for c in FEATURE_COLUMNS},
            notes=[],
            game_date=game.game_date,
        )

    linescore = {"teams": {"home": {"runs": 5}, "away": {"runs": 3}}}
    box = {
        "teams": {
            "home": {
                "players": {
                    "ID99": {
                        "person": {"id": 99},
                        "stats": {"pitching": {"gamesStarted": 1}},
                    }
                }
            },
            "away": {
                "players": {
                    "ID88": {
                        "person": {"id": 88},
                        "stats": {"pitching": {"gamesStarted": 1}},
                    }
                }
            },
        }
    }

    seen_ids: list[tuple[int | None, int | None]] = []

    def capture_features(game, cache_dir=None, **kwargs):
        seen_ids.append((game.home_probable_id, game.away_probable_id))
        return fake_features(game, cache_dir=cache_dir, **kwargs)

    with patch(
        "baseball_analyze.models.train.iter_completed_games",
        return_value=games,
    ), patch(
        "baseball_analyze.models.train.fetch_linescore",
        return_value=linescore,
    ), patch(
        "baseball_analyze.models.train.fetch_boxscore",
        return_value=box,
    ), patch(
        "baseball_analyze.models.train.build_features_for_game",
        side_effect=capture_features,
    ):
        X, y, rows = build_training_sample(
            seasons=[2024],
            cache_dir=None,
            max_games=None,
            starter_source="probable",
        )

    assert len(rows) == 2
    assert seen_ids[0] == (10, 20)  # schedule probables kept
    assert seen_ids[1] == (99, 88)  # boxscore fallback when TBD
    assert X.shape == (2, len(FEATURE_COLUMNS))
    assert list(y) == [1, 1]


def test_as_of_team_offense_table_indexes_ops():
    from baseball_analyze.data import fangraphs_features as fg

    splits = [
        {
            "player": {"id": 1},
            "team": {"id": 147, "abbreviation": "NYY"},
            "stat": {"plateAppearances": 100, "ops": ".800", "obp": ".350", "slg": ".450"},
        },
        {
            "player": {"id": 2},
            "team": {"id": 111, "abbreviation": "BOS"},
            "stat": {"plateAppearances": 100, "ops": ".700", "obp": ".320", "slg": ".380"},
        },
    ]

    with patch(
        "baseball_analyze.data.fangraphs_features.fetch_stats_by_date_range",
        return_value=splits,
    ), patch(
        "baseball_analyze.data.fangraphs_features.team_id_to_abbrev_map",
        return_value={147: "NYY", 111: "BOS"},
    ), patch(
        "baseball_analyze.data.fangraphs_features.mlb_abbrev_to_fangraphs",
        side_effect=lambda ab, _s: ab,
    ):
        table = fg._as_of_team_offense_table(2024, "2024-06-01")

    assert "NYY" in table.index
    assert "BOS" in table.index
    assert table.loc["NYY", "wRC+"] > table.loc["BOS", "wRC+"]
