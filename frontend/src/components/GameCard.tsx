import Link from "next/link";

import { PredictionDisplay } from "@/components/PredictionDisplay";
import type { Game, Prediction } from "@/lib/types";

function teamLabel(team: Game["home_team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

function formatScore(game: Game): string | null {
  if (game.home_score == null || game.away_score == null) {
    return null;
  }
  return `${game.away_score} – ${game.home_score}`;
}

export function GameCard({
  game,
  prediction,
}: {
  game: Game;
  prediction?: Prediction | null;
}) {
  const score = formatScore(game);

  return (
    <Link
      href={`/games/${game.game_pk}`}
      className={`group block rounded-xl border bg-surface p-4 transition-colors hover:border-accent/40 hover:bg-surface-elevated ${
        game.followed
          ? "border-accent/35 ring-1 ring-accent/15"
          : "border-border"
      }`}
    >
      <div className="mb-3 flex items-center justify-between gap-3 text-xs text-muted">
        <div className="flex min-w-0 items-center gap-2">
          <span>{game.detailed_state}</span>
          {game.followed ? (
            <span className="rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent">
              Following
            </span>
          ) : null}
        </div>
        {game.venue_name ? <span className="truncate">{game.venue_name}</span> : null}
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate font-medium">{teamLabel(game.away_team)}</p>
            <p className="text-xs text-muted">{game.away_team.abbreviation}</p>
          </div>
          {score ? (
            <span className="font-mono text-lg tabular-nums">{game.away_score}</span>
          ) : null}
        </div>
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate font-medium">{teamLabel(game.home_team)}</p>
            <p className="text-xs text-muted">{game.home_team.abbreviation}</p>
          </div>
          {score ? (
            <span className="font-mono text-lg tabular-nums">{game.home_score}</span>
          ) : null}
        </div>
      </div>
      {prediction ? (
        <PredictionDisplay game={game} prediction={prediction} variant="card" />
      ) : !score && (game.away_probable_pitcher || game.home_probable_pitcher) ? (
        <p className="mt-3 text-xs text-muted">
          {game.away_probable_pitcher?.full_name ?? "TBD"} vs{" "}
          {game.home_probable_pitcher?.full_name ?? "TBD"}
        </p>
      ) : null}
      <p className="mt-3 text-xs text-accent opacity-0 transition-opacity group-hover:opacity-100">
        View live game →
      </p>
    </Link>
  );
}
