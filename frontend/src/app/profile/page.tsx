"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { useAuth } from "@/contexts/AuthContext";
import {
  ApiError,
  fetchFollows,
  fetchTeams,
  followTeam,
  unfollow,
} from "@/lib/api";
import type { Follow, Team } from "@/lib/types";

function teamLabel(team: Team): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

export default function ProfilePage() {
  const { user } = useAuth();
  const [teams, setTeams] = useState<Team[]>([]);
  const [follows, setFollows] = useState<Follow[]>([]);
  const [selectedTeamId, setSelectedTeamId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const teamFollows = useMemo(
    () => follows.filter((follow) => follow.entity_type === "team" && follow.team),
    [follows],
  );
  const playerFollows = useMemo(
    () =>
      follows.filter((follow) => follow.entity_type === "player" && follow.player),
    [follows],
  );
  const followedTeamIds = useMemo(
    () => new Set(teamFollows.map((follow) => follow.team!.id)),
    [teamFollows],
  );
  const availableTeams = useMemo(
    () => teams.filter((team) => !followedTeamIds.has(team.id)),
    [teams, followedTeamIds],
  );

  async function reloadWatchlist() {
    const [nextTeams, nextFollows] = await Promise.all([
      fetchTeams(),
      fetchFollows(),
    ]);
    setTeams(nextTeams);
    setFollows(nextFollows);
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        await reloadWatchlist();
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Failed to load watchlist.",
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

  async function handleFollowTeam(event: FormEvent) {
    event.preventDefault();
    const teamId = Number(selectedTeamId);
    if (!Number.isFinite(teamId) || teamId <= 0) {
      return;
    }

    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await followTeam(teamId);
      await reloadWatchlist();
      setSelectedTeamId("");
      setMessage("Team added to your watchlist.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to follow team.");
    } finally {
      setSaving(false);
    }
  }

  async function handleUnfollow(followId: number) {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await unfollow(followId);
      await reloadWatchlist();
      setMessage("Removed from your watchlist.");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to unfollow.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <ProtectedRoute>
      <AppShell>
        <section className="space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
            <p className="mt-1 text-sm text-muted">
              Account settings and watchlist.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-surface p-6">
            <h2 className="text-sm font-medium text-muted">Account</h2>
            <dl className="mt-4 space-y-3 text-sm">
              <div className="flex flex-col gap-1 sm:flex-row sm:justify-between">
                <dt className="text-muted">Email</dt>
                <dd>{user?.email}</dd>
              </div>
              <div className="flex flex-col gap-1 sm:flex-row sm:justify-between">
                <dt className="text-muted">Member since</dt>
                <dd>
                  {user?.created_at
                    ? new Date(user.created_at).toLocaleDateString()
                    : "—"}
                </dd>
              </div>
            </dl>
          </div>

          <div className="space-y-4 rounded-xl border border-border bg-surface p-6">
            <div>
              <h2 className="text-sm font-medium">Watchlist</h2>
              <p className="mt-1 text-sm text-muted">
                Follow teams to prioritize their games and predictions on the
                home schedule.
              </p>
            </div>

            {error ? (
              <p className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger">
                {error}
              </p>
            ) : null}
            {message ? (
              <p className="rounded-lg border border-accent/30 bg-accent/10 px-4 py-3 text-sm text-accent">
                {message}
              </p>
            ) : null}

            {loading ? (
              <p className="text-sm text-muted">Loading watchlist…</p>
            ) : (
              <>
                <form
                  onSubmit={handleFollowTeam}
                  className="flex flex-col gap-3 sm:flex-row sm:items-end"
                >
                  <label className="flex min-w-0 flex-1 flex-col gap-1.5 text-sm">
                    <span className="text-muted">Add a team</span>
                    <select
                      value={selectedTeamId}
                      onChange={(event) => setSelectedTeamId(event.target.value)}
                      disabled={saving || availableTeams.length === 0}
                      className="rounded-lg border border-border bg-background px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2 disabled:opacity-60"
                    >
                      <option value="">
                        {availableTeams.length === 0
                          ? "All teams followed"
                          : "Select a team"}
                      </option>
                      {availableTeams.map((team) => (
                        <option key={team.id} value={team.id}>
                          {team.abbreviation} — {teamLabel(team)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="submit"
                    disabled={saving || !selectedTeamId}
                    className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground transition-opacity disabled:opacity-50"
                  >
                    Follow
                  </button>
                </form>

                <div className="space-y-3">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
                    Teams
                  </h3>
                  {teamFollows.length === 0 ? (
                    <p className="text-sm text-muted">
                      No teams followed yet. Add one above after the schedule has
                      synced teams into the database.
                    </p>
                  ) : (
                    <ul className="divide-y divide-border rounded-lg border border-border">
                      {teamFollows.map((follow) => (
                        <li
                          key={follow.id}
                          className="flex items-center justify-between gap-3 px-4 py-3 text-sm"
                        >
                          <div className="min-w-0">
                            <p className="truncate font-medium">
                              {teamLabel(follow.team!)}
                            </p>
                            <p className="text-xs text-muted">
                              {follow.team!.abbreviation}
                            </p>
                          </div>
                          <button
                            type="button"
                            disabled={saving}
                            onClick={() => void handleUnfollow(follow.id)}
                            className="shrink-0 rounded-md border border-border px-3 py-1.5 text-muted transition-colors hover:border-danger/40 hover:text-danger disabled:opacity-50"
                          >
                            Unfollow
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {playerFollows.length > 0 ? (
                  <div className="space-y-3">
                    <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
                      Players
                    </h3>
                    <ul className="divide-y divide-border rounded-lg border border-border">
                      {playerFollows.map((follow) => (
                        <li
                          key={follow.id}
                          className="flex items-center justify-between gap-3 px-4 py-3 text-sm"
                        >
                          <p className="truncate font-medium">
                            {follow.player!.full_name}
                          </p>
                          <button
                            type="button"
                            disabled={saving}
                            onClick={() => void handleUnfollow(follow.id)}
                            className="shrink-0 rounded-md border border-border px-3 py-1.5 text-muted transition-colors hover:border-danger/40 hover:text-danger disabled:opacity-50"
                          >
                            Unfollow
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </>
            )}
          </div>
        </section>
      </AppShell>
    </ProtectedRoute>
  );
}
