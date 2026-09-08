"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiError, fetchMatchupAnalytics } from "@/lib/api";
import type { MatchupAnalytics } from "@/lib/types";

function formatMetric(value: number | null | undefined, digits = 1): string {
  if (value == null) {
    return "—";
  }
  return value.toFixed(digits);
}

function teamLabel(team: MatchupAnalytics["home_team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

function MatchupContent() {
  const searchParams = useSearchParams();
  const currentYear = new Date().getFullYear();
  const homeTeamId = Number(searchParams.get("home"));
  const awayTeamId = Number(searchParams.get("away"));
  const seasonParam = Number(searchParams.get("season") || currentYear);
  const matchupValid =
    Number.isFinite(homeTeamId) && Number.isFinite(awayTeamId);

  const [data, setData] = useState<MatchupAnalytics | null>(null);
  const [loading, setLoading] = useState(matchupValid);
  const [error, setError] = useState<string | null>(
    matchupValid
      ? null
      : "Choose home and away teams from the Analytics hub.",
  );

  useEffect(() => {
    if (!matchupValid) {
      return;
    }

    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const next = await fetchMatchupAnalytics(
          homeTeamId,
          awayTeamId,
          seasonParam,
        );
        if (!cancelled) {
          setData(next);
        }
      } catch (err) {
        if (!cancelled) {
          setData(null);
          setError(
            err instanceof ApiError
              ? err.message
              : "Failed to load matchup analytics.",
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
  }, [homeTeamId, awayTeamId, matchupValid, seasonParam]);

  return (
    <section className="space-y-6">
      <div>
        <Link href="/analytics" className="text-sm text-muted hover:text-foreground">
          ← Analytics
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {data
            ? `${data.away_team.abbreviation} @ ${data.home_team.abbreviation}`
            : "Matchup"}
        </h1>
        <p className="mt-1 text-sm text-muted">
          Head-to-head results for {seasonParam}.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-muted">Loading matchup…</p>
      ) : error ? (
        <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : data ? (
        <>
          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Series summary</h2>
            <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                label="Meetings"
                value={String(data.head_to_head.meetings)}
              />
              <MetricCard
                label={data.home_team.abbreviation}
                value={`${data.head_to_head.home_wins} W`}
              />
              <MetricCard
                label={data.away_team.abbreviation}
                value={`${data.head_to_head.away_wins} W`}
              />
              <MetricCard
                label="wRC+ edge"
                value={
                  data.fangraphs_diff?.wrc_plus != null
                    ? formatMetric(data.fangraphs_diff.wrc_plus, 0)
                    : "—"
                }
              />
            </dl>
            <p className="mt-4 text-xs text-muted">
              {teamLabel(data.away_team)} at {teamLabel(data.home_team)}. FanGraphs
              FIP edge (home better when positive):{" "}
              {formatMetric(data.fangraphs_diff?.team_fip)}.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Games</h2>
            {data.head_to_head.games.length === 0 ? (
              <p className="mt-3 text-sm text-muted">
                No stored meetings between these teams this season.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[28rem] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
                      <th className="pb-3 pr-4 font-medium">Date</th>
                      <th className="pb-3 pr-4 font-medium">Matchup</th>
                      <th className="pb-3 pr-4 font-medium">Score</th>
                      <th className="pb-3 font-medium">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.head_to_head.games.map((game) => (
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
                          {game.venue_away_abbreviation} @{" "}
                          {game.venue_home_abbreviation}
                        </td>
                        <td className="py-3 pr-4 font-mono tabular-nums">
                          {game.away_score != null && game.home_score != null
                            ? `${game.away_score}–${game.home_score}`
                            : "—"}
                        </td>
                        <td className="py-3 text-muted">{game.detailed_state}</td>
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

export default function MatchupAnalyticsPage() {
  return (
    <ProtectedRoute>
      <AppShell>
        <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
          <MatchupContent />
        </Suspense>
      </AppShell>
    </ProtectedRoute>
  );
}
