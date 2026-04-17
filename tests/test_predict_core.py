from unittest.mock import patch

from baseball_analyze.features import FeatureRow
from baseball_analyze.mlb_client import ScheduledGame


def _game(pk: int, state: str) -> ScheduledGame:
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


def test_build_feature_rows_skips_postponed_and_collects_notes() -> None:
    fr = FeatureRow(
        game_pk=1,
        season=2025,
        home_fg="NYY",
        away_fg="BOS",
        features={"diff_wrc_plus": 1.0},
        notes=["x"],
    )

    with patch("baseball_analyze.predict_core.build_features_for_game") as build_one:
        build_one.return_value = fr

        from baseball_analyze.predict_core import build_feature_rows

        rows, notes = build_feature_rows([_game(1, "Scheduled"), _game(2, "Postponed")])

    assert len(rows) == 1
    assert rows[0].game_pk == 1
    assert notes == [(1, ["x"])]

