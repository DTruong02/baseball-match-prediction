"""CLI: pregame home win probabilities for a date or single game."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from baseball_analyze.mlb_client import fetch_schedule_by_game_pk, fetch_schedule_for_date
from baseball_analyze.model import load_artifact
from baseball_analyze.models.inference import predict_game

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
        _model, _cols = load_artifact(model_path)
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

    results: list[dict] = []
    for g in games:
        if g.detailed_state in ("Postponed", "Cancelled"):
            continue
        try:
            results.append(predict_game(g.game_pk, model_path, cache_dir=cache_dir))
        except ValueError as e:
            typer.echo(f"[skip] {e}", err=True)

    if not results:
        typer.echo("No games found (or all postponed).")
        raise typer.Exit(code=1)

    for result in results:
        p = float(result["home_win_proba"])
        typer.echo(
            f"{result['away_fg']} @ {result['home_fg']} (pk={result['game_pk']})  P(home)={p:.3f}"
        )
        if explain:
            for c, v in result["features"].items():
                typer.echo(f"  {c}: {float(v):+.3f}")
        for n in result.get("notes") or []:
            typer.echo(f"[note game {result['game_pk']}] {n}", err=True)


@app.command("chat")
def chat_cmd(
    model_path: Path = typer.Option(
        Path("artifacts/model.joblib"),
        "--model",
        "-m",
        help="Trained joblib pipeline from baseball-analyze-train.",
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="FanGraphs cache directory.",
    ),
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        help="Optional OpenAI-compatible base URL (e.g. http://localhost:11434/v1).",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key (cloud) or placeholder (local). Also supports OPENAI_API_KEY/LLM_API_KEY.",
    ),
    llm_model: Optional[str] = typer.Option(
        None,
        "--llm-model",
        help="LLM model name. Also supports OPENAI_MODEL/LLM_MODEL.",
    ),
) -> None:
    """Interactive chat that calls tools for grounded predictions."""
    from baseball_analyze.chat_repl import run_repl

    run_repl(
        model_path=model_path,
        cache_dir=cache_dir,
        base_url=base_url,
        api_key=api_key,
        llm_model=llm_model,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
