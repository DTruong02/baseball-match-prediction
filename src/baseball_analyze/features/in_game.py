"""In-game (live WP) feature engineering from play-by-play / linescore state.

Distinct from pregame features in ``baseball_analyze.features``: these encode
score, inning, outs, base state, current batter/pitcher context, and bullpen
proxies suitable for an in-game win-probability model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import numpy as np

from baseball_analyze.data.fangraphs_features import bullpen_fip_by_team
from baseball_analyze.data.mlb_client import (
    fetch_pitcher_season_fip_xfip,
    fetch_pitcher_season_kbb9,
)
from baseball_analyze.data.team_mapping import mlb_abbrev_to_fangraphs

IN_GAME_FEATURE_COLUMNS: list[str] = [
    "score_diff",
    "inning",
    "is_top_inning",
    "outs",
    "runner_on_1b",
    "runner_on_2b",
    "runner_on_3b",
    "balls",
    "strikes",
    "pitcher_fip",
    "pitcher_kbb9",
    "same_hand_matchup",
    "defense_bullpen_fip",
    "is_reliever",
]


@dataclass
class InGameState:
    """Snapshot of game situation used for in-game feature construction."""

    home_score: int
    away_score: int
    inning: int
    is_top_inning: bool
    outs: int
    balls: int = 0
    strikes: int = 0
    runner_on_1b: bool = False
    runner_on_2b: bool = False
    runner_on_3b: bool = False
    pitcher_id: Optional[int] = None
    batter_id: Optional[int] = None
    pitch_hand: Optional[str] = None
    bat_side: Optional[str] = None
    is_reliever: bool = False
    at_bat_index: Optional[int] = None


@dataclass
class InGameFeatureRow:
    game_pk: int
    season: int
    at_bat_index: Optional[int]
    features: dict[str, float]
    notes: list[str] = field(default_factory=list)
    label_home_win: Optional[bool] = None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _hand_code(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if code in ("R", "L"):
        return str(code)
    return None


def _runner_occupied(matchup: dict[str, Any], key: str) -> bool:
    return bool(matchup.get(key))


def _bases_from_matchup_post(matchup: dict[str, Any]) -> tuple[bool, bool, bool]:
    return (
        _runner_occupied(matchup, "postOnFirst"),
        _runner_occupied(matchup, "postOnSecond"),
        _runner_occupied(matchup, "postOnThird"),
    )


def _bases_from_offense(offense: dict[str, Any]) -> tuple[bool, bool, bool]:
    return (
        bool(offense.get("onFirst")),
        bool(offense.get("onSecond")),
        bool(offense.get("onThird")),
    )


def _half_key(inning: int, is_top: bool) -> tuple[int, bool]:
    return (inning, is_top)


def iter_pre_play_states(live_feed: dict[str, Any]) -> Iterator[InGameState]:
    """
    Yield game state at the **start** of each completed at-bat.

    Reconstructs outs and bases from prior plays within the same half-inning.
    Balls/strikes are 0-0 (start of PA); use ``state_from_linescore`` for live
    mid-count snapshots.
    """
    live_data = live_feed.get("liveData") or {}
    plays_section = live_data.get("plays") or {}
    all_plays = plays_section.get("allPlays") or []

    home_score = 0
    away_score = 0
    outs = 0
    on_1b = on_2b = on_3b = False
    prev_half: Optional[tuple[int, bool]] = None
    first_pitcher_home: Optional[int] = None
    first_pitcher_away: Optional[int] = None

    for play in all_plays:
        about = play.get("about") or {}
        if not about.get("isComplete"):
            continue

        at_bat_index = _optional_int(about.get("atBatIndex"))
        inning = _optional_int(about.get("inning"))
        is_top = _optional_bool(about.get("isTopInning"))
        if inning is None or is_top is None:
            continue

        half = _half_key(inning, is_top)
        if prev_half is not None and half != prev_half:
            outs = 0
            on_1b = on_2b = on_3b = False
        prev_half = half

        matchup = play.get("matchup") or {}
        pitcher = matchup.get("pitcher") or {}
        batter = matchup.get("batter") or {}
        pitcher_id = _optional_int(pitcher.get("id"))
        batter_id = _optional_int(batter.get("id"))

        if pitcher_id is not None:
            if is_top:
                if first_pitcher_home is None:
                    first_pitcher_home = pitcher_id
                is_reliever = pitcher_id != first_pitcher_home
            else:
                if first_pitcher_away is None:
                    first_pitcher_away = pitcher_id
                is_reliever = pitcher_id != first_pitcher_away
        else:
            is_reliever = False

        yield InGameState(
            home_score=home_score,
            away_score=away_score,
            inning=inning,
            is_top_inning=is_top,
            outs=outs,
            balls=0,
            strikes=0,
            runner_on_1b=on_1b,
            runner_on_2b=on_2b,
            runner_on_3b=on_3b,
            pitcher_id=pitcher_id,
            batter_id=batter_id,
            pitch_hand=_hand_code(matchup.get("pitchHand")),
            bat_side=_hand_code(matchup.get("batSide")),
            is_reliever=is_reliever,
            at_bat_index=at_bat_index,
        )

        result = play.get("result") or {}
        post_home = _optional_int(result.get("homeScore"))
        post_away = _optional_int(result.get("awayScore"))
        if post_home is not None:
            home_score = post_home
        if post_away is not None:
            away_score = post_away

        count = play.get("count") or {}
        post_outs = _optional_int(count.get("outs"))
        if post_outs is not None:
            outs = post_outs

        on_1b, on_2b, on_3b = _bases_from_matchup_post(matchup)


def state_from_linescore(live_feed: dict[str, Any]) -> Optional[InGameState]:
    """
    Build current in-game state from ``liveData.linescore`` (live inference).

    Returns None when inning / half-inning cannot be resolved.
    """
    live_data = live_feed.get("liveData") or {}
    linescore = live_data.get("linescore") or {}
    teams = linescore.get("teams") or {}

    inning = _optional_int(linescore.get("currentInning"))
    is_top = _optional_bool(linescore.get("isTopInning"))
    if inning is None or is_top is None:
        return None

    home_runs = _optional_int((teams.get("home") or {}).get("runs")) or 0
    away_runs = _optional_int((teams.get("away") or {}).get("runs")) or 0

    offense = linescore.get("offense") or {}
    defense = linescore.get("defense") or {}
    on_1b, on_2b, on_3b = _bases_from_offense(offense)

    pitcher = defense.get("pitcher") or {}
    batter = offense.get("batter") or {}

    # Reliever heuristic: compare current pitcher to first pitcher seen in plays.
    pitcher_id = _optional_int(pitcher.get("id"))
    is_reliever = False
    if pitcher_id is not None:
        first_home: Optional[int] = None
        first_away: Optional[int] = None
        for snap in iter_pre_play_states(live_feed):
            if snap.pitcher_id is None:
                continue
            if snap.is_top_inning and first_home is None:
                first_home = snap.pitcher_id
            elif (not snap.is_top_inning) and first_away is None:
                first_away = snap.pitcher_id
            if first_home is not None and first_away is not None:
                break
        starter = first_home if is_top else first_away
        if starter is not None:
            is_reliever = pitcher_id != starter

    # Prefer count / hands from currentPlay when present.
    plays_section = live_data.get("plays") or {}
    current = plays_section.get("currentPlay") or {}
    matchup = current.get("matchup") or {}
    count = current.get("count") or {}

    pitch_hand = _hand_code(matchup.get("pitchHand"))
    bat_side = _hand_code(matchup.get("batSide"))
    balls = _optional_int(count.get("balls"))
    strikes = _optional_int(count.get("strikes"))
    if balls is None:
        balls = _optional_int(linescore.get("balls")) or 0
    if strikes is None:
        strikes = _optional_int(linescore.get("strikes")) or 0

    at_bat_index = _optional_int((current.get("about") or {}).get("atBatIndex"))

    return InGameState(
        home_score=home_runs,
        away_score=away_runs,
        inning=inning,
        is_top_inning=is_top,
        outs=_optional_int(linescore.get("outs")) or 0,
        balls=balls,
        strikes=strikes,
        runner_on_1b=on_1b,
        runner_on_2b=on_2b,
        runner_on_3b=on_3b,
        pitcher_id=pitcher_id or _optional_int((matchup.get("pitcher") or {}).get("id")),
        batter_id=_optional_int(batter.get("id"))
        or _optional_int((matchup.get("batter") or {}).get("id")),
        pitch_hand=pitch_hand,
        bat_side=bat_side,
        is_reliever=is_reliever,
        at_bat_index=at_bat_index,
    )


def _pitcher_stats(
    pitcher_id: Optional[int],
    season: int,
    cache: dict[int, tuple[float, float]],
    notes: list[str],
) -> tuple[float, float]:
    """Return (fip, kbb9) with league-ish defaults when missing."""
    default_fip = 4.20
    default_kbb9 = 0.0
    if pitcher_id is None:
        notes.append("Missing pitcher id; using default pitcher stats.")
        return default_fip, default_kbb9
    if pitcher_id in cache:
        return cache[pitcher_id]

    fip, _ = fetch_pitcher_season_fip_xfip(pitcher_id, season)
    kbb9 = fetch_pitcher_season_kbb9(pitcher_id, season)
    if fip is None:
        notes.append(f"No FIP for pitcher mlbam={pitcher_id}; using default.")
        fip = default_fip
    if kbb9 is None:
        notes.append(f"No K-BB/9 for pitcher mlbam={pitcher_id}; using default.")
        kbb9 = default_kbb9
    cache[pitcher_id] = (float(fip), float(kbb9))
    return cache[pitcher_id]


def build_in_game_features(
    state: InGameState,
    *,
    game_pk: int,
    season: int,
    home_abbrev: str,
    away_abbrev: str,
    cache_dir: Optional[Path] = None,
    pitcher_stats_cache: Optional[dict[int, tuple[float, float]]] = None,
    bullpen_by_team: Optional[dict[str, float]] = None,
    label_home_win: Optional[bool] = None,
) -> InGameFeatureRow:
    """
    Map an ``InGameState`` into the fixed ``IN_GAME_FEATURE_COLUMNS`` vector.

    Bullpen proxy is the **defending** (pitching) team's season bullpen FIP.
    Higher ``score_diff`` favors the home team.
    """
    notes: list[str] = []
    stats_cache = pitcher_stats_cache if pitcher_stats_cache is not None else {}

    home_fg = mlb_abbrev_to_fangraphs(home_abbrev, season)
    away_fg = mlb_abbrev_to_fangraphs(away_abbrev, season)

    if bullpen_by_team is None:
        bull = bullpen_fip_by_team(season, cache_dir=cache_dir)
        bullpen_by_team = {str(k): float(v) for k, v in bull.items() if v is not None and not (isinstance(v, float) and np.isnan(v))}

    defending_fg = home_fg if state.is_top_inning else away_fg
    defense_bullpen = float(bullpen_by_team.get(defending_fg, 4.20))
    if defending_fg not in bullpen_by_team:
        notes.append(f"Missing bullpen FIP for {defending_fg}; using default.")

    pitcher_fip, pitcher_kbb9 = _pitcher_stats(
        state.pitcher_id, season, stats_cache, notes
    )

    same_hand = 0.0
    if state.pitch_hand and state.bat_side and state.pitch_hand == state.bat_side:
        same_hand = 1.0

    feats = {
        "score_diff": float(state.home_score - state.away_score),
        "inning": float(state.inning),
        "is_top_inning": 1.0 if state.is_top_inning else 0.0,
        "outs": float(state.outs),
        "runner_on_1b": 1.0 if state.runner_on_1b else 0.0,
        "runner_on_2b": 1.0 if state.runner_on_2b else 0.0,
        "runner_on_3b": 1.0 if state.runner_on_3b else 0.0,
        "balls": float(state.balls),
        "strikes": float(state.strikes),
        "pitcher_fip": pitcher_fip,
        "pitcher_kbb9": pitcher_kbb9,
        "same_hand_matchup": same_hand,
        "defense_bullpen_fip": defense_bullpen,
        "is_reliever": 1.0 if state.is_reliever else 0.0,
    }

    return InGameFeatureRow(
        game_pk=game_pk,
        season=season,
        at_bat_index=state.at_bat_index,
        features=feats,
        notes=notes,
        label_home_win=label_home_win,
    )


def build_in_game_features_from_feed(
    live_feed: dict[str, Any],
    *,
    game_pk: int,
    season: int,
    home_abbrev: str,
    away_abbrev: str,
    cache_dir: Optional[Path] = None,
) -> Optional[InGameFeatureRow]:
    """Live-inference helper: features from current linescore state."""
    state = state_from_linescore(live_feed)
    if state is None:
        return None
    return build_in_game_features(
        state,
        game_pk=game_pk,
        season=season,
        home_abbrev=home_abbrev,
        away_abbrev=away_abbrev,
        cache_dir=cache_dir,
    )


def build_in_game_training_rows_from_feed(
    live_feed: dict[str, Any],
    *,
    game_pk: int,
    season: int,
    home_abbrev: str,
    away_abbrev: str,
    home_won: bool,
    cache_dir: Optional[Path] = None,
) -> list[InGameFeatureRow]:
    """
    Build one training row per completed at-bat from historical play-by-play.

    All rows share the game's final ``home_won`` label (standard WP training).
    """
    bull = bullpen_fip_by_team(season, cache_dir=cache_dir)
    bullpen_by_team = {
        str(k): float(v)
        for k, v in bull.items()
        if v is not None and not (isinstance(v, float) and np.isnan(v))
    }
    stats_cache: dict[int, tuple[float, float]] = {}
    rows: list[InGameFeatureRow] = []
    for state in iter_pre_play_states(live_feed):
        rows.append(
            build_in_game_features(
                state,
                game_pk=game_pk,
                season=season,
                home_abbrev=home_abbrev,
                away_abbrev=away_abbrev,
                cache_dir=cache_dir,
                pitcher_stats_cache=stats_cache,
                bullpen_by_team=bullpen_by_team,
                label_home_win=home_won,
            )
        )
    return rows


def in_game_feature_vector(row: InGameFeatureRow) -> np.ndarray:
    return np.array([row.features[c] for c in IN_GAME_FEATURE_COLUMNS], dtype=float)


def in_game_features_to_matrix(rows: list[InGameFeatureRow]) -> np.ndarray:
    if not rows:
        return np.zeros((0, len(IN_GAME_FEATURE_COLUMNS)), dtype=float)
    return np.vstack([in_game_feature_vector(r) for r in rows])
