"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiError, fetchTeams } from "@/lib/api";
import { teamLabel } from "@/lib/teams";
import type { Team } from "@/lib/types";

export default function AnalyticsHubPage() {
  const router = useRouter();
  const currentYear = new Date().getFullYear();
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamId, setTeamId] = useState("");
  const [homeTeamId, setHomeTeamId] = useState("");
  const [awayTeamId, setAwayTeamId] = useState("");
  const [season, setSeason] = useState(String(currentYear));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchTeams();
        if (!cancelled) {
          setTeams(data);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Failed to load teams.",
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
  }, []);

  function openTeam(event: FormEvent) {
    event.preventDefault();
    if (!teamId) {
      return;
    }
    const params = new URLSearchParams({ season });
    router.push(`/teams/${teamId}?${params.toString()}`);
  }

  function openMatchup(event: FormEvent) {
    event.preventDefault();
    if (!homeTeamId || !awayTeamId) {
      return;
    }
    const params = new URLSearchParams({
      home: homeTeamId,
      away: awayTeamId,
      season,
    });
    router.push(`/analytics/matchup?${params.toString()}`);
  }

  return (
    <ProtectedRoute>
      <AppShell>
        <section className="space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
            <p className="mt-1 text-sm text-muted">
              Team trends, pitcher starts, and head-to-head matchups from stored
              games and FanGraphs caches.
            </p>
          </div>

          {error ? (
            <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
              {error}
            </p>
          ) : null}

          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Season</h2>
            <label className="mt-4 flex max-w-xs flex-col gap-1.5 text-sm">
              <span className="text-muted">Year</span>
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

          <form
            onSubmit={openTeam}
            className="space-y-4 rounded-xl border border-border bg-surface p-6"
          >
            <div>
              <h2 className="text-sm font-medium">Team trends</h2>
              <p className="mt-1 text-sm text-muted">
                Monthly record and run differential for one club.
              </p>
            </div>
            {loading ? (
              <p className="text-sm text-muted">Loading teams…</p>
            ) : (
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <label className="flex min-w-0 flex-1 flex-col gap-1.5 text-sm">
                  <span className="text-muted">Team</span>
                  <select
                    value={teamId}
                    onChange={(event) => setTeamId(event.target.value)}
                    className="rounded-lg border border-border bg-background px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
                  >
                    <option value="">Select a team</option>
                    {teams.map((team) => (
                      <option key={team.id} value={team.id}>
                        {team.abbreviation} — {teamLabel(team)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="submit"
                  disabled={!teamId}
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-opacity disabled:opacity-50"
                >
                  Open team
                </button>
              </div>
            )}
          </form>

          <form
            onSubmit={openMatchup}
            className="space-y-4 rounded-xl border border-border bg-surface p-6"
          >
            <div>
              <h2 className="text-sm font-medium">Matchup</h2>
              <p className="mt-1 text-sm text-muted">
                Head-to-head results and FanGraphs season diffs.
              </p>
            </div>
            {loading ? (
              <p className="text-sm text-muted">Loading teams…</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="text-muted">Home side</span>
                  <select
                    value={homeTeamId}
                    onChange={(event) => setHomeTeamId(event.target.value)}
                    className="rounded-lg border border-border bg-background px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
                  >
                    <option value="">Select home</option>
                    {teams.map((team) => (
                      <option key={team.id} value={team.id}>
                        {team.abbreviation} — {teamLabel(team)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="text-muted">Away side</span>
                  <select
                    value={awayTeamId}
                    onChange={(event) => setAwayTeamId(event.target.value)}
                    className="rounded-lg border border-border bg-background px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2"
                  >
                    <option value="">Select away</option>
                    {teams.map((team) => (
                      <option key={team.id} value={team.id}>
                        {team.abbreviation} — {teamLabel(team)}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="submit"
                  disabled={!homeTeamId || !awayTeamId || homeTeamId === awayTeamId}
                  className="rounded-lg border border-border px-4 py-2 text-sm transition-colors hover:border-accent/40 sm:col-span-2"
                >
                  Compare matchup
                </button>
              </div>
            )}
          </form>

          <p className="text-sm text-muted">
            Tip: open a followed team or pitcher from{" "}
            <Link href="/profile" className="text-accent hover:underline">
              Profile
            </Link>
            .
          </p>
        </section>
      </AppShell>
    </ProtectedRoute>
  );
}
