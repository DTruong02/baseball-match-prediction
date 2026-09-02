import json
from pathlib import Path
from unittest.mock import patch

from baseball_analyze.data.mlb_client import _parse_game, fetch_live_feed

FIXTURE = Path(__file__).parent / "fixtures" / "schedule_game.json"


def test_parse_schedule_game_fixture() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    g = _parse_game(raw)
    assert g.game_pk == 778001
    assert g.season == 2025
    assert g.home_abbrev == "NYY"
    assert g.away_abbrev == "BOS"
    assert g.home_probable_id == 592866
    assert g.away_probable_id == 669203
    assert g.venue_id == 3313
    assert g.venue_name == "Yankee Stadium"
    assert g.home_probable_name == "Trevor Williams"
    assert g.away_probable_name == "Corbin Burnes"
    assert g.home_score is None
    assert g.away_score is None


@patch("baseball_analyze.data.mlb_client._get")
@patch("baseball_analyze.data.mlb_client.fetch_schedule_by_game_pk")
def test_fetch_live_feed_fallback_assembles_payload(
    mock_schedule: object,
    mock_get: object,
) -> None:
    from baseball_analyze.data.mlb_client import ScheduledGame, fetch_live_feed

    mock_schedule.return_value = ScheduledGame(
        game_pk=824239,
        game_date="2026-08-15",
        season=2026,
        status="Final",
        detailed_state="Final",
        home_team_id=147,
        away_team_id=111,
        home_abbrev="NYY",
        away_abbrev="BOS",
        venue_id=None,
        home_probable_id=None,
        away_probable_id=None,
    )

    def _get_side_effect(path: str, params=None, **kwargs):  # type: ignore[no-untyped-def]
        if path.endswith("/feed/live"):
            from baseball_analyze.data.mlb_client import MLBAPIError

            raise MLBAPIError("not found")
        if path.endswith("/playByPlay"):
            return {"allPlays": [{"about": {"atBatIndex": 0}, "result": {"event": "Single"}}]}
        if path.endswith("/linescore"):
            return {
                "currentInning": 9,
                "teams": {"home": {"runs": 3}, "away": {"runs": 4}},
            }
        raise AssertionError(f"unexpected path {path}")

    mock_get.side_effect = _get_side_effect

    feed = fetch_live_feed(824239)
    assert feed["gameData"]["status"]["detailedState"] == "Final"
    assert len(feed["liveData"]["plays"]["allPlays"]) == 1
    assert feed["liveData"]["linescore"]["currentInning"] == 9
