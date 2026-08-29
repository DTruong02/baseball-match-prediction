import Link from "next/link";

import type { Game } from "@/lib/types";

function teamLabel(team: Game["home_team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

function formatScore(game: Game): string | null {
  if (game.home_score == null || game.away_score == null) {
    return null;
  }
  return `${game.away_score} – ${game.home_score}`;
}

export function GameCard({ game }: { game: Game }) {
  const score = formatScore(game);

  return (
    <Link
      href={`/games/${game.game_pk}`}
      className="group block rounded-xl border border-border bg-surface p-4 transition-colors hover:border-accent/40 hover:bg-surface-elevated"
    >
      <div className="mb-3 flex items-center justify-between gap-3 text-xs text-muted">
        <span>{game.detailed_state}</span>
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
      {!score && (game.away_probable_pitcher || game.home_probable_pitcher) ? (
        <p className="mt-3 text-xs text-muted">
          {game.away_probable_pitcher?.full_name ?? "TBD"} vs{" "}
          {game.home_probable_pitcher?.full_name ?? "TBD"}
        </p>
      ) : null}
      <p className="mt-3 text-xs text-accent opacity-0 transition-opacity group-hover:opacity-100">
        View game details →
      </p>
    </Link>
  );
}
