"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiError, fetchGame, fetchPrediction } from "@/lib/api";
import type { Game } from "@/lib/types";

function teamLabel(team: Game["home_team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

export default function GameDetailPage() {
  const params = useParams<{ gamePk: string }>();
  const gamePk = Number(params.gamePk);
  const invalidGamePk = !Number.isFinite(gamePk);
  const [game, setGame] = useState<Game | null>(null);
  const [hasPrediction, setHasPrediction] = useState(false);
  const [loading, setLoading] = useState(!invalidGamePk);
  const [error, setError] = useState<string | null>(
    invalidGamePk ? "Invalid game id." : null,
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
        const [gameData, prediction] = await Promise.all([
          fetchGame(gamePk),
          fetchPrediction(gamePk),
        ]);
        if (!cancelled) {
          setGame(gameData);
          setHasPrediction(prediction != null);
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
              <header className="space-y-2">
                <p className="text-sm text-muted">
                  {game.game_date} · {game.detailed_state}
                </p>
                <h1 className="text-2xl font-semibold tracking-tight">
                  {teamLabel(game.away_team)} at {teamLabel(game.home_team)}
                </h1>
                {game.venue_name ? (
                  <p className="text-sm text-muted">{game.venue_name}</p>
                ) : null}
              </header>

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
                      {game.away_score != null ? ` · ${game.away_score} runs` : ""}
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
                      {game.home_score != null ? ` · ${game.home_score} runs` : ""}
                    </p>
                    <p className="mt-2 text-sm text-muted">
                      SP: {game.home_probable_pitcher?.full_name ?? "TBD"}
                    </p>
                  </div>
                </div>
              </section>

              <section className="rounded-xl border border-border bg-surface p-6">
                <h2 className="text-sm font-medium text-muted">Pregame prediction</h2>
                {hasPrediction ? (
                  <p className="mt-3 text-sm">Prediction available.</p>
                ) : (
                  <p className="mt-3 text-sm text-muted">
                    Predictions will appear here once the model is integrated in
                    Stage 3.
                  </p>
                )}
              </section>
            </>
          ) : null}
        </div>
      </AppShell>
    </ProtectedRoute>
  );
}
