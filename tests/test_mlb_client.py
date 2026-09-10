import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from baseball_analyze.data.mlb_client import (
    MLBAPIError,
    _clear_pitcher_stat_caches_for_tests,
    _get,
    _parse_game,
    _reset_http_client_for_tests,
    _reset_outbound_rate_limiter_for_tests,
    fetch_live_feed,
    fetch_pitcher_season_fip_xfip,
    prefetch_season_pitcher_stats,
)

FIXTURE = Path(__file__).parent / "fixtures" / "schedule_game.json"


@pytest.fixture(autouse=True)
def _disable_outbound_rate_limit() -> None:
    _reset_outbound_rate_limiter_for_tests(0.0)
    _reset_http_client_for_tests()
    _clear_pitcher_stat_caches_for_tests()
    yield
    _reset_outbound_rate_limiter_for_tests(0.0)
    _reset_http_client_for_tests()
    _clear_pitcher_stat_caches_for_tests()


def test_parse_schedule_game_fixture() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    g = _parse_game(raw)
    assert g.game_pk == 778001
    # officialDate wins over UTC calendar day of gameDate (2025-04-07T02:05Z).
    assert g.game_date == "2025-04-06"
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


def test_parse_game_falls_back_to_game_date_when_official_missing() -> None:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del raw["officialDate"]
    g = _parse_game(raw)
    assert g.game_date == "2025-04-07"


@patch("baseball_analyze.data.mlb_client._get")
@patch("baseball_analyze.data.mlb_client.fetch_schedule_by_game_pk")
def test_fetch_live_feed_fallback_assembles_payload(
    mock_schedule: object,
    mock_get: object,
) -> None:
    from baseball_analyze.data.mlb_client import ScheduledGame

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


def test_get_retries_on_429_with_retry_after() -> None:
    response_429 = MagicMock()
    response_429.status_code = 429
    response_429.text = "rate limited"
    response_429.headers = {"Retry-After": "0.01"}

    response_ok = MagicMock()
    response_ok.status_code = 200
    response_ok.json.return_value = {"ok": True}
    response_ok.headers = {}

    client = MagicMock()
    client.get.side_effect = [response_429, response_ok]

    with (
        patch("baseball_analyze.data.mlb_client._shared_http_client", return_value=client),
        patch("baseball_analyze.data.mlb_client.time.sleep") as mock_sleep,
    ):
        payload = _get("/teams", retries=1, backoff_s=0.5)

    assert payload == {"ok": True}
    mock_sleep.assert_called_once()
    assert mock_sleep.call_args.args[0] == pytest.approx(0.01)


def test_get_does_not_retry_client_errors() -> None:
    response_404 = MagicMock()
    response_404.status_code = 404
    response_404.text = "missing"
    response_404.headers = {}

    client = MagicMock()
    client.get.return_value = response_404

    with (
        patch("baseball_analyze.data.mlb_client._shared_http_client", return_value=client),
        patch("baseball_analyze.data.mlb_client.time.sleep") as mock_sleep,
    ):
        with pytest.raises(MLBAPIError, match="404"):
            _get("/teams", retries=2, backoff_s=0.01)

    mock_sleep.assert_not_called()
    assert client.get.call_count == 1


def test_get_enforces_outbound_rate_limit() -> None:
    _reset_outbound_rate_limiter_for_tests(0.05)
    response_ok = MagicMock()
    response_ok.status_code = 200
    response_ok.json.return_value = {"ok": True}
    response_ok.headers = {}

    client = MagicMock()
    client.get.return_value = response_ok

    with patch("baseball_analyze.data.mlb_client._shared_http_client", return_value=client):
        started = __import__("time").monotonic()
        _get("/teams", retries=0)
        _get("/teams", retries=0)
        elapsed = __import__("time").monotonic() - started

    assert elapsed >= 0.045


def test_pitcher_fip_is_cached() -> None:
    with patch(
        "baseball_analyze.data.mlb_client._get",
        return_value={
            "stats": [{"splits": [{"stat": {"fip": 3.21, "xfip": 3.45}}]}],
        },
    ) as mock_get:
        first = fetch_pitcher_season_fip_xfip(1, 2023)
        second = fetch_pitcher_season_fip_xfip(1, 2023)

    assert first == (3.21, 3.45)
    assert second == (3.21, 3.45)
    assert mock_get.call_count == 1


def test_kbb9_handles_mlb_placeholder_rates() -> None:
    from baseball_analyze.data.mlb_client import _kbb9_from_stat

    # MLB sometimes returns "-.--" for rate stats with little/no IP.
    assert _kbb9_from_stat(
        {
            "strikeoutsPer9Inn": "-.--",
            "walksPer9Inn": "-.--",
            "inningsPitched": "0.0",
            "strikeOuts": 0,
            "baseOnBalls": 0,
        }
    ) is None

    val = _kbb9_from_stat(
        {
            "strikeoutsPer9Inn": "-.--",
            "walksPer9Inn": "2.00",
            "inningsPitched": "9.0",
            "strikeOuts": 9,
            "baseOnBalls": 2,
        }
    )
    assert val == pytest.approx(7.0)


def test_prefetch_season_pitcher_stats_warms_caches() -> None:
    season_payload = {
        "stats": [
            {
                "splits": [
                    {
                        "player": {"id": 10},
                        "stat": {
                            "inningsPitched": "100.0",
                            "homeRuns": 10,
                            "baseOnBalls": 20,
                            "hitByPitch": 2,
                            "strikeOuts": 90,
                            "strikeoutsPer9Inn": 8.1,
                            "walksPer9Inn": 1.8,
                        },
                    }
                ]
            }
        ]
    }
    saber_payload = {
        "stats": [
            {
                "splits": [
                    {"player": {"id": 10}, "stat": {"fip": 3.5, "xfip": 3.6}},
                ]
            }
        ]
    }

    def _side_effect(path: str, params=None, **kwargs):  # type: ignore[no-untyped-def]
        if path == "/stats" and params and params.get("stats") == "season":
            return season_payload
        if path == "/stats" and params and params.get("stats") == "sabermetrics":
            return saber_payload
        raise AssertionError(f"unexpected {path} {params}")

    with patch("baseball_analyze.data.mlb_client._get", side_effect=_side_effect):
        warmed = prefetch_season_pitcher_stats(2023)
        fip = fetch_pitcher_season_fip_xfip(10, 2023)

    assert warmed["filled_fip"] >= 1
    assert warmed["filled_kbb9"] >= 1
    assert fip == (3.5, 3.6)
