import type { Game, Prediction } from "@/lib/types";
import { favoredSide, formatWinProbability } from "@/lib/predictions";

function teamLabel(team: Game["home_team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

interface PredictionDisplayProps {
  game: Game;
  prediction: Prediction;
  variant?: "card" | "detail";
}

export function PredictionDisplay({
  game,
  prediction,
  variant = "detail",
}: PredictionDisplayProps) {
  const favorite = favoredSide(prediction);
  const awayPct = formatWinProbability(prediction.away_win_proba);
  const homePct = formatWinProbability(prediction.home_win_proba);

  if (variant === "card") {
    return (
      <div className="mt-3 space-y-1 border-t border-border pt-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          Model pick
        </p>
        <div className="flex items-center justify-between gap-2 text-sm">
          <span
            className={
              favorite === "away" ? "font-medium text-accent" : "text-muted"
            }
          >
            {game.away_team.abbreviation} {awayPct}
          </span>
          <span className="text-xs text-muted">/</span>
          <span
            className={
              favorite === "home" ? "font-medium text-accent" : "text-muted"
            }
          >
            {game.home_team.abbreviation} {homePct}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <ProbabilityBar
          label={teamLabel(game.away_team)}
          abbreviation={game.away_team.abbreviation}
          probability={prediction.away_win_proba}
          favored={favorite === "away"}
        />
        <ProbabilityBar
          label={teamLabel(game.home_team)}
          abbreviation={game.home_team.abbreviation}
          probability={prediction.home_win_proba}
          favored={favorite === "home"}
        />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span>Model: {prediction.model_version.run_id}</span>
        <span>
          Generated{" "}
          {new Date(prediction.created_at).toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })}
        </span>
      </div>
      {prediction.notes ? (
        <p className="text-sm text-muted">{prediction.notes}</p>
      ) : null}
    </div>
  );
}

function ProbabilityBar({
  label,
  abbreviation,
  probability,
  favored,
}: {
  label: string;
  abbreviation: string;
  probability: number;
  favored: boolean;
}) {
  const pct = formatWinProbability(probability);
  const width = Math.round(probability * 100);

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{label}</p>
          <p className="text-xs text-muted">{abbreviation}</p>
        </div>
        <span
          className={`font-mono text-lg tabular-nums ${
            favored ? "text-accent" : "text-foreground"
          }`}
        >
          {pct}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-surface-elevated">
        <div
          className={`h-full rounded-full transition-all ${
            favored ? "bg-accent" : "bg-border"
          }`}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}
