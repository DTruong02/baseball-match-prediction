"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiError, fetchPlayerAnalytics } from "@/lib/api";
import type { PlayerAnalytics } from "@/lib/types";

function formatPct(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function formatMetric(value: number | null | undefined, digits = 2): string {
  if (value == null) {
    return "—";
  }
  return value.toFixed(digits);
}

function PlayerAnalyticsContent() {
  const params = useParams<{ playerId: string }>();
  const searchParams = useSearchParams();
  const playerId = Number(params.playerId);
  const playerIdValid = Number.isFinite(playerId);
  const currentYear = new Date().getFullYear();
  const initialSeason = Number(searchParams.get("season") || currentYear);

  const [season, setSeason] = useState(String(initialSeason));
  const [data, setData] = useState<PlayerAnalytics | null>(null);
  const [loading, setLoading] = useState(playerIdValid);
  const [error, setError] = useState<string | null>(
    playerIdValid ? null : "Invalid player id.",
  );

  useEffect(() => {
    if (!playerIdValid) {
      return;
    }

    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      const parsedSeason = Number(season);
      if (!Number.isFinite(parsedSeason)) {
        setError("Enter a valid season.");
        setLoading(false);
        return;
      }
      try {
        const next = await fetchPlayerAnalytics(playerId, parsedSeason);
        if (!cancelled) {
          setData(next);
        }
      } catch (err) {
        if (!cancelled) {
          setData(null);
          setError(
            err instanceof ApiError
              ? err.message
              : "Failed to load player analytics.",
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [playerId, playerIdValid, season]);

  return (
    <section className="space-y-6">
      <div>
        <Link href="/analytics" className="text-sm text-muted hover:text-foreground">
          ← Analytics
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {data?.player.full_name ?? "Player performance"}
        </h1>
        <p className="mt-1 text-sm text-muted">
          Probable-start outcomes and light play-event splits.
        </p>
      </div>

      <div className="rounded-xl border border-border bg-surface p-6">
        <label className="flex max-w-xs flex-col gap-1.5 text-sm">
          <span className="text-muted">Season</span>
          <input
            type="number"
            min={2000}
            max={2100}
            value={season}
            onChange={(event) => setSeason(event.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
          />
        </label>
      </div>

      {loading ? (
        <p className="text-sm text-muted">Loading player analytics…</p>
      ) : error ? (
        <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : data ? (
        <>
          <div className="rounded-xl border border-border bg-surface p-6">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-sm font-medium text-muted">Summary</h2>
              {data.player.team ? (
                <Link
                  href={`/teams/${data.player.team.id}?season=${season}`}
                  className="text-xs text-accent hover:underline"
                >
                  {data.player.team.abbreviation}
                </Link>
              ) : null}
            </div>
            <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                label="Starts"
                value={String(data.probable_starts.games)}
              />
              <MetricCard
                label="Team W–L"
                value={`${data.probable_starts.wins}–${data.probable_starts.losses}`}
              />
              <MetricCard
                label="Win %"
                value={formatPct(data.probable_starts.win_pct)}
              />
              <MetricCard
                label="RBI (events)"
                value={String(data.event_splits.rbi)}
              />
            </dl>
            {data.fangraphs ? (
              <p className="mt-4 text-xs text-muted">
                FanGraphs: FIP {formatMetric(data.fangraphs.fip)} · ERA{" "}
                {formatMetric(data.fangraphs.era)} · IP{" "}
                {formatMetric(data.fangraphs.ip, 1)} · K/9{" "}
                {formatMetric(data.fangraphs.k_per_9, 1)}
              </p>
            ) : (
              <p className="mt-4 text-xs text-muted">
                No FanGraphs pitcher row matched for this name/season.
              </p>
            )}
          </div>

          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Probable starts</h2>
            {data.probable_starts.games_detail.length === 0 ? (
              <p className="mt-3 text-sm text-muted">
                No probable-pitcher appearances for this season.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[32rem] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
                      <th className="pb-3 pr-4 font-medium">Date</th>
                      <th className="pb-3 pr-4 font-medium">Opp</th>
                      <th className="pb-3 pr-4 font-medium">Site</th>
                      <th className="pb-3 pr-4 font-medium">Score</th>
                      <th className="pb-3 font-medium">Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.probable_starts.games_detail.map((game) => (
                      <tr
                        key={game.game_pk}
                        className="border-b border-border/60 last:border-0"
                      >
                        <td className="py-3 pr-4 font-mono tabular-nums">
                          <Link
                            href={`/games/${game.game_pk}`}
                            className="hover:text-accent"
                          >
                            {game.game_date}
                          </Link>
                        </td>
                        <td className="py-3 pr-4">
                          {game.opponent_abbreviation}
                        </td>
                        <td className="py-3 pr-4 text-muted">
                          {game.is_home ? "Home" : "Away"}
                        </td>
                        <td className="py-3 pr-4 font-mono tabular-nums">
                          {game.team_score != null && game.opponent_score != null
                            ? `${game.team_score}–${game.opponent_score}`
                            : "—"}
                        </td>
                        <td className="py-3 font-mono tabular-nums">
                          {game.result ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-surface-elevated px-4 py-3">
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1 font-mono text-xl tabular-nums">{value}</dd>
    </div>
  );
}

export default function PlayerAnalyticsPage() {
  return (
    <ProtectedRoute>
      <AppShell>
        <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
          <PlayerAnalyticsContent />
        </Suspense>
      </AppShell>
    </ProtectedRoute>
  );
}
