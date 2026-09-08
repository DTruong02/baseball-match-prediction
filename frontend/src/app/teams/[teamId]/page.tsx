"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ApiError, fetchTeamAnalytics } from "@/lib/api";
import type { TeamAnalytics } from "@/lib/types";

function formatPct(value: number | null | undefined): string {
  if (value == null) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

function formatMetric(value: number | null | undefined, digits = 1): string {
  if (value == null) {
    return "—";
  }
  return value.toFixed(digits);
}

function teamLabel(team: TeamAnalytics["team"]): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

function TeamAnalyticsContent() {
  const params = useParams<{ teamId: string }>();
  const searchParams = useSearchParams();
  const teamId = Number(params.teamId);
  const currentYear = new Date().getFullYear();
  const initialSeason = Number(searchParams.get("season") || currentYear);

  const [season, setSeason] = useState(String(initialSeason));
  const [data, setData] = useState<TeamAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!Number.isFinite(teamId)) {
      setError("Invalid team id.");
      setLoading(false);
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
        const next = await fetchTeamAnalytics(teamId, parsedSeason);
        if (!cancelled) {
          setData(next);
        }
      } catch (err) {
        if (!cancelled) {
          setData(null);
          setError(
            err instanceof ApiError ? err.message : "Failed to load team analytics.",
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
  }, [teamId, season]);

  return (
    <section className="space-y-6">
      <div>
        <Link href="/analytics" className="text-sm text-muted hover:text-foreground">
          ← Analytics
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {data ? teamLabel(data.team) : "Team trends"}
        </h1>
        <p className="mt-1 text-sm text-muted">
          Season record with monthly wins, losses, and run differential.
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
        <p className="text-sm text-muted">Loading team analytics…</p>
      ) : error ? (
        <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </p>
      ) : data ? (
        <>
          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Season summary</h2>
            <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                label="Record"
                value={`${data.record.wins}–${data.record.losses}`}
              />
              <MetricCard label="Run diff" value={String(data.record.run_diff)} />
              <MetricCard
                label="Home"
                value={`${data.splits.home.wins}–${data.splits.home.losses}`}
              />
              <MetricCard
                label="Model accuracy"
                value={formatPct(data.prediction_accuracy.accuracy)}
              />
            </dl>
            {data.fangraphs ? (
              <p className="mt-4 text-xs text-muted">
                FanGraphs {data.fangraphs.fangraphs_team}: wRC+{" "}
                {formatMetric(data.fangraphs.wrc_plus, 0)} · FIP{" "}
                {formatMetric(data.fangraphs.team_fip)} · bullpen{" "}
                {formatMetric(data.fangraphs.bullpen_fip)}
              </p>
            ) : (
              <p className="mt-4 text-xs text-muted">
                FanGraphs season stats unavailable for this season.
              </p>
            )}
          </div>

          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Monthly trend</h2>
            {data.monthly_trend.length === 0 ? (
              <p className="mt-3 text-sm text-muted">
                No final scored games for this season yet.
              </p>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[28rem] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border text-xs uppercase tracking-wide text-muted">
                      <th className="pb-3 pr-4 font-medium">Month</th>
                      <th className="pb-3 pr-4 font-medium">W–L</th>
                      <th className="pb-3 pr-4 font-medium">RS</th>
                      <th className="pb-3 pr-4 font-medium">RA</th>
                      <th className="pb-3 font-medium">Diff</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.monthly_trend.map((row) => (
                      <tr
                        key={row.month}
                        className="border-b border-border/60 last:border-0"
                      >
                        <td className="py-3 pr-4 font-mono tabular-nums">
                          {row.month}
                        </td>
                        <td className="py-3 pr-4 font-mono tabular-nums">
                          {row.wins}–{row.losses}
                        </td>
                        <td className="py-3 pr-4 font-mono tabular-nums">
                          {row.runs_scored}
                        </td>
                        <td className="py-3 pr-4 font-mono tabular-nums">
                          {row.runs_allowed}
                        </td>
                        <td className="py-3 font-mono tabular-nums">
                          {row.run_diff > 0 ? `+${row.run_diff}` : row.run_diff}
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

export default function TeamAnalyticsPage() {
  return (
    <ProtectedRoute>
      <AppShell>
        <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
          <TeamAnalyticsContent />
        </Suspense>
      </AppShell>
    </ProtectedRoute>
  );
}
