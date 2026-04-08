"""CLI: pregame home win probabilities for a date or single game."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import typer

from baseball_analyze.features import (
    FEATURE_COLUMNS,
    build_features_for_game,
    feature_vector,
)
from baseball_analyze.model import load_artifact, predict_home_win_proba
from baseball_analyze.mlb_client import fetch_schedule_by_game_pk, fetch_schedule_for_date

app = typer.Typer(help="Pregame MLB home win probability (v1).", no_args_is_help=True)


@app.command("predict")
def predict_cmd(
    date: Optional[str] = typer.Option(
        None,
        "--date",
        "-d",
        help="Game date YYYY-MM-DD (all games that day).",
    ),
    game_pk: Optional[int] = typer.Option(
        None,
        "--game-pk",
        "-g",
        help="Single MLB gamePk (optional; can be used without --date).",
    ),
    model_path: Path = typer.Option(
        Path("artifacts/model.joblib"),
        "--model",
        "-m",
        help="Trained joblib pipeline from baseball-analyze-train.",
    ),
    explain: bool = typer.Option(
        False,
        "--explain",
        "-e",
        help="Show signed feature values (not coefficients unless linear pipeline).",
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="FanGraphs cache directory.",
    ),
) -> None:
    """Print P(home win) for scheduled game(s). Train a model first with `baseball-analyze-train run`."""
    if date is None and game_pk is None:
        raise typer.BadParameter("Provide --date and/or --game-pk.")

    try:
        model, _cols = load_artifact(model_path)
    except (FileNotFoundError, OSError):
        typer.echo(
            f"Model not found at {model_path}. Run: baseball-analyze-train --max-games 400",
            err=True,
        )
        raise typer.Exit(code=1)

    games = []
    if date is not None:
        games = fetch_schedule_for_date(date)
    if game_pk is not None:
        if games:
            games = [g for g in games if g.game_pk == game_pk]
            if not games:
                raise typer.BadParameter(f"No game {game_pk} on {date}.")
        else:
            g = fetch_schedule_by_game_pk(game_pk)
            if g is None:
                raise typer.BadParameter(f"Could not load schedule for gamePk={game_pk}.")
            games = [g]

    rows = []
    notes_all: list[tuple[int, list[str]]] = []
    for g in games:
        if g.detailed_state in ("Postponed", "Cancelled"):
            continue
        fr = build_features_for_game(g, cache_dir=cache_dir)
        rows.append(fr)
        if fr.notes:
            notes_all.append((g.game_pk, fr.notes))

    if not rows:
        typer.echo("No games found (or all postponed).")
        raise typer.Exit(code=1)

    X = np.vstack([feature_vector(r) for r in rows])
    proba = predict_home_win_proba(model, X)

    for fr, p in zip(rows, proba):
        typer.echo(
            f"{fr.away_fg} @ {fr.home_fg} (pk={fr.game_pk})  P(home)={p:.3f}"
        )
        if explain:
            for c, v in zip(FEATURE_COLUMNS, feature_vector(fr)):
                typer.echo(f"  {c}: {v:+.3f}")

    for pk, ns in notes_all:
        for n in ns:
            typer.echo(f"[note game {pk}] {n}", err=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
