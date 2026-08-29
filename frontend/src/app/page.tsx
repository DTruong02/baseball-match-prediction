"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { GameCard } from "@/components/GameCard";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiError, fetchGames } from "@/lib/api";
import type { Game } from "@/lib/types";

function todayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function DashboardPage() {
  const [selectedDate, setSelectedDate] = useState(todayIsoDate);
  const [games, setGames] = useState<Game[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadGames() {
      setLoading(true);
      setError(null);

      try {
        const data = await fetchGames(selectedDate);
        if (!cancelled) {
          setGames(data);
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof ApiError) {
            setError(err.message);
          } else {
            setError("Failed to load games.");
          }
          setGames([]);
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
  }, [selectedDate]);

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
                MLB games synced to the platform database.
              </p>
            </div>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="text-muted">Date</span>
              <input
                type="date"
                value={selectedDate}
                onChange={(event) => setSelectedDate(event.target.value)}
                className="rounded-lg border border-border bg-surface px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
              />
            </label>
          </div>

          {loading ? (
            <p className="text-sm text-muted">Loading games…</p>
          ) : error ? (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
              {error}
            </p>
          ) : games.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-surface/60 px-6 py-12 text-center">
              <p className="font-medium">No games for this date</p>
              <p className="mt-2 text-sm text-muted">
                Sync the schedule from the API if you have not loaded this date
                yet.
              </p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {games.map((game) => (
                <GameCard key={game.game_pk} game={game} />
              ))}
            </div>
          )}
        </section>
      </AppShell>
    </ProtectedRoute>
  );
}
