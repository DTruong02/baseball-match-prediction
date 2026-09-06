"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { EventTimeline } from "@/components/EventTimeline";
import { LiveConnectionBadge } from "@/components/LiveConnectionBadge";
import { LiveScoreboard } from "@/components/LiveScoreboard";
import { PredictionDisplay } from "@/components/PredictionDisplay";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { useLiveGame } from "@/hooks/useLiveGame";
import { ApiError, fetchGame, fetchPrediction } from "@/lib/api";
import type { Game, Prediction } from "@/lib/types";

function teamLabel(team: Game["home_team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

export default function GameDetailPage() {
  const params = useParams<{ gamePk: string }>();
  const gamePk = Number(params.gamePk);
  const invalidGamePk = !Number.isFinite(gamePk);
  const [game, setGame] = useState<Game | null>(null);
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [loading, setLoading] = useState(!invalidGamePk);
  const [error, setError] = useState<string | null>(
    invalidGamePk ? "Invalid game id." : null,
  );

  const { live, events, connectionStatus, degraded } = useLiveGame(
    invalidGamePk ? null : gamePk,
  );

  useEffect(() => {
    if (invalidGamePk) {
      return;
    }

    let cancelled = false;

    async function loadGame() {
      setLoading(true);
      setError(null);

      try {
        const [gameData, predictionData] = await Promise.all([
          fetchGame(gamePk),
          fetchPrediction(gamePk),
        ]);
        if (!cancelled) {
          setGame(gameData);
          setPrediction(
            predictionData ?? gameData.pregame_prediction ?? null,
          );
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setError(err.message);
          } else {
            setError("Failed to load game.");
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadGame();

    return () => {
      cancelled = true;
    };
  }, [gamePk, invalidGamePk]);

  return (
    <ProtectedRoute>
      <AppShell>
        <div className="space-y-6">
          <Link
            href="/"
            className="inline-flex text-sm text-muted transition-colors hover:text-foreground"
          >
            ← Back to schedule
          </Link>

          {loading ? (
            <p className="text-sm text-muted">Loading game…</p>
          ) : error ? (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
              {error}
            </p>
          ) : game ? (
            <>
              <header className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-muted">
                    {game.game_date} ·{" "}
                    {live?.detailed_state ?? game.detailed_state}
                  </p>
                  <LiveConnectionBadge
                    status={connectionStatus}
                    degraded={degraded}
                  />
                </div>
                <h1 className="text-2xl font-semibold tracking-tight">
                  {teamLabel(game.away_team)} at {teamLabel(game.home_team)}
                </h1>
                {game.venue_name ? (
                  <p className="text-sm text-muted">{game.venue_name}</p>
                ) : null}
              </header>

              <LiveScoreboard game={game} live={live} />

              <EventTimeline events={events} />

              <section className="rounded-xl border border-border bg-surface p-6">
                <h2 className="text-sm font-medium text-muted">Matchup</h2>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted">
                      Away
                    </p>
                    <p className="mt-1 text-lg font-medium">
                      {teamLabel(game.away_team)}
                    </p>
                    <p className="text-sm text-muted">
                      {game.away_team.abbreviation}
                    </p>
                    <p className="mt-2 text-sm text-muted">
                      SP: {game.away_probable_pitcher?.full_name ?? "TBD"}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted">
                      Home
                    </p>
                    <p className="mt-1 text-lg font-medium">
                      {teamLabel(game.home_team)}
                    </p>
                    <p className="text-sm text-muted">
                      {game.home_team.abbreviation}
                    </p>
                    <p className="mt-2 text-sm text-muted">
                      SP: {game.home_probable_pitcher?.full_name ?? "TBD"}
                    </p>
                  </div>
                </div>
              </section>

              <section className="rounded-xl border border-border bg-surface p-6">
                <h2 className="text-sm font-medium text-muted">
                  Pregame prediction
                </h2>
                {prediction ? (
                  <PredictionDisplay
                    game={game}
                    prediction={prediction}
                    variant="detail"
                  />
                ) : (
                  <p className="mt-3 text-sm text-muted">
                    No pregame prediction is available for this game yet.
                  </p>
                )}
              </section>

              {game.live_prediction ||
              (live?.home_win_proba != null && live?.away_win_proba != null) ? (
                <section className="rounded-xl border border-border bg-surface p-6">
                  <h2 className="text-sm font-medium text-muted">
                    Live win probability
                  </h2>
                  <p className="mt-3 text-sm text-muted">
                    Updates on runs, outs, pitching changes, and end of inning.
                    Current line also appears on the live scoreboard. Major WP
                    swings get a short rule-based note (for example, scoring
                    plays).
                  </p>
                  {live?.wp_explanation ? (
                    <p className="mt-3 text-sm text-foreground">
                      Latest swing: {live.wp_explanation}
                    </p>
                  ) : null}
                  {game.live_prediction ? (
                    <div className="mt-4">
                      <PredictionDisplay
                        game={game}
                        prediction={game.live_prediction}
                        variant="detail"
                      />
                    </div>
                  ) : null}
                </section>
              ) : null}
            </>
          ) : null}
        </div>
      </AppShell>
    </ProtectedRoute>
  );
}
