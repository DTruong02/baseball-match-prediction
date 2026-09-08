"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { GameCard } from "@/components/GameCard";
import { LiveDegradedBanner } from "@/components/LiveDegradedBanner";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import {
  ApiError,
  fetchFollows,
  fetchGames,
  fetchHealth,
  fetchPrediction,
} from "@/lib/api";
import type { Game, Prediction } from "@/lib/types";

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function DashboardPage() {
  const [selectedDate, setSelectedDate] = useState(todayIsoDate);
  const [followingOnly, setFollowingOnly] = useState(false);
  const [hasFollows, setHasFollows] = useState(false);
  const [games, setGames] = useState<Game[]>([]);
  const [predictions, setPredictions] = useState<
    Record<number, Prediction | null>
  >({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [liveDegraded, setLiveDegraded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadFollows() {
      try {
        const follows = await fetchFollows();
        if (!cancelled) {
          setHasFollows(follows.length > 0);
        }
      } catch {
        if (!cancelled) {
          setHasFollows(false);
        }
      }
    }

    void loadFollows();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadLiveHealth() {
      try {
        const health = await fetchHealth();
        if (!cancelled) {
          setLiveDegraded(Boolean(health.live_degraded));
        }
      } catch {
        if (!cancelled) {
          setLiveDegraded(false);
        }
      }
    }

    void loadLiveHealth();
    const timer = window.setInterval(() => {
      void loadLiveHealth();
    }, 30_000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadGames() {
      setLoading(true);
      setError(null);

      try {
        const data = await fetchGames(selectedDate, { followingOnly });
        const predictionResults = await Promise.all(
          data.map(async (game) => {
            try {
              const prediction = await fetchPrediction(game.game_pk);
              return [game.game_pk, prediction] as const;
            } catch {
              return [game.game_pk, null] as const;
            }
          }),
        );
        if (!cancelled) {
          setGames(data);
          setPredictions(Object.fromEntries(predictionResults));
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setError(err.message);
          } else {
            setError("Failed to load games.");
          }
          setGames([]);
          setPredictions({});
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void loadGames();

    return () => {
      cancelled = true;
    };
  }, [selectedDate, followingOnly]);

  const followedGames = games.filter((game) => game.followed);
  const otherGames = games.filter((game) => !game.followed);
  const showSections = !followingOnly && followedGames.length > 0;

  return (
    <ProtectedRoute>
      <AppShell>
        <section className="space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">
                Today&apos;s schedule
              </h1>
              <p className="mt-1 text-sm text-muted">
                {hasFollows
                  ? "Followed teams appear first with their predictions."
                  : "MLB games synced to the platform database."}
              </p>
              {!hasFollows ? (
                <p className="mt-2 text-sm text-muted">
                  <Link href="/profile" className="text-accent hover:underline">
                    Follow teams on your profile
                  </Link>{" "}
                  to personalize this slate.
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-3 sm:items-end">
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="text-muted">Date</span>
                <input
                  type="date"
                  value={selectedDate}
                  onChange={(event) => setSelectedDate(event.target.value)}
                  className="rounded-lg border border-border bg-surface px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
                />
              </label>
              {hasFollows ? (
                <label className="flex items-center gap-2 text-sm text-muted">
                  <input
                    type="checkbox"
                    checked={followingOnly}
                    onChange={(event) => setFollowingOnly(event.target.checked)}
                    className="accent-[var(--accent)]"
                  />
                  Following only
                </label>
              ) : null}
            </div>
          </div>

          <LiveDegradedBanner show={liveDegraded} />

          {loading ? (
            <p className="text-sm text-muted">Loading games…</p>
          ) : error ? (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
              {error}
            </p>
          ) : games.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-surface/60 px-6 py-12 text-center">
              <p className="font-medium">
                {followingOnly
                  ? "No followed teams on this date"
                  : "No games for this date"}
              </p>
              <p className="mt-2 text-sm text-muted">
                {followingOnly
                  ? "Try another date, or clear the Following only filter."
                  : "Sync the schedule from the API if you have not loaded this date yet."}
              </p>
            </div>
          ) : showSections ? (
            <div className="space-y-8">
              <div className="space-y-4">
                <h2 className="text-sm font-medium text-muted">Your teams</h2>
                <div className="grid gap-4 sm:grid-cols-2">
                  {followedGames.map((game) => (
                    <GameCard
                      key={game.game_pk}
                      game={game}
                      prediction={predictions[game.game_pk]}
                    />
                  ))}
                </div>
              </div>
              {otherGames.length > 0 ? (
                <div className="space-y-4">
                  <h2 className="text-sm font-medium text-muted">Rest of slate</h2>
                  <div className="grid gap-4 sm:grid-cols-2">
                    {otherGames.map((game) => (
                      <GameCard
                        key={game.game_pk}
                        game={game}
                        prediction={predictions[game.game_pk]}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {games.map((game) => (
                <GameCard
                  key={game.game_pk}
                  game={game}
                  prediction={predictions[game.game_pk]}
                />
              ))}
            </div>
          )}
        </section>
      </AppShell>
    </ProtectedRoute>
  );
}
