import json
from pathlib import Path

from baseball_analyze.data.mlb_client import _parse_game

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
