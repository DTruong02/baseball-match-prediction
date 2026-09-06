import type { Game, LiveState } from "@/lib/types";

function teamLabel(team: Game["home_team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

function formatInning(live: LiveState): string {
  if (live.current_inning == null) {
    return live.detailed_state || live.status;
  }
  const half =
    live.inning_state ??
    (live.is_top_inning == null
      ? ""
      : live.is_top_inning
        ? "Top"
        : "Bot");
  return half ? `${half} ${live.current_inning}` : `Inning ${live.current_inning}`;
}

function CountDots({
  label,
  filled,
  total,
}: {
  label: string;
  filled: number | null;
  total: number;
}) {
  if (filled == null) {
    return null;
  }
  return (
    <div className="flex items-center gap-2">
      <span className="w-10 text-xs uppercase tracking-wide text-muted">
        {label}
      </span>
      <div className="flex gap-1" aria-label={`${filled} ${label}`}>
        {Array.from({ length: total }, (_, index) => (
          <span
            key={index}
            className={`h-2.5 w-2.5 rounded-full ${
              index < filled ? "bg-accent" : "bg-border"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

export function LiveScoreboard({
  game,
  live,
}: {
  game: Game;
  live: LiveState | null;
}) {
  const awayScore = live?.away_score ?? game.away_score ?? 0;
  const homeScore = live?.home_score ?? game.home_score ?? 0;
  const statusLine = live
    ? formatInning(live)
    : game.detailed_state || game.status;

  return (
    <section className="rounded-xl border border-border bg-surface p-6">
      <div className="flex items-start justify-between gap-4">
        <h2 className="text-sm font-medium text-muted">Scoreboard</h2>
        <p className="text-sm text-muted">{statusLine}</p>
      </div>

      <div className="mt-6 space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="truncate text-lg font-medium">
              {teamLabel(game.away_team)}
            </p>
            <p className="text-xs text-muted">{game.away_team.abbreviation}</p>
          </div>
          <span className="font-mono text-3xl tabular-nums tracking-tight">
            {awayScore}
          </span>
        </div>
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="truncate text-lg font-medium">
              {teamLabel(game.home_team)}
            </p>
            <p className="text-xs text-muted">{game.home_team.abbreviation}</p>
          </div>
          <span className="font-mono text-3xl tabular-nums tracking-tight">
            {homeScore}
          </span>
        </div>
      </div>

      {live &&
      (live.outs != null || live.balls != null || live.strikes != null) ? (
        <div className="mt-6 space-y-2 border-t border-border pt-4">
          <CountDots label="Balls" filled={live.balls} total={4} />
          <CountDots label="Strikes" filled={live.strikes} total={3} />
          <CountDots label="Outs" filled={live.outs} total={3} />
        </div>
      ) : null}

      {live?.home_win_proba != null && live?.away_win_proba != null ? (
        <div className="mt-6 border-t border-border pt-4">
          <h3 className="text-xs uppercase tracking-wide text-muted">
            Live win probability
          </h3>
          <div className="mt-3 grid grid-cols-2 gap-4">
            <div>
              <p className="text-xs text-muted">{game.away_team.abbreviation}</p>
              <p className="mt-1 font-mono text-xl tabular-nums">
                {Math.round(live.away_win_proba * 100)}%
              </p>
            </div>
            <div className="text-right">
              <p className="text-xs text-muted">{game.home_team.abbreviation}</p>
              <p className="mt-1 font-mono text-xl tabular-nums">
                {Math.round(live.home_win_proba * 100)}%
              </p>
            </div>
          </div>
          {live.wp_explanation ? (
            <p
              className={`mt-3 text-sm ${
                live.wp_delta_home != null && live.wp_delta_home > 0
                  ? "text-accent"
                  : live.wp_delta_home != null && live.wp_delta_home < 0
                    ? "text-danger"
                    : "text-muted"
              }`}
            >
              {live.wp_explanation}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
