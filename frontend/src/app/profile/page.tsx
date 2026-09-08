"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { useAuth } from "@/contexts/AuthContext";
import {
  ApiError,
  fetchFollows,
  fetchNotificationPreferences,
  fetchNotifications,
  fetchTeams,
  followTeam,
  markAllNotificationsRead,
  markNotificationRead,
  unfollow,
  updateNotificationPreferences,
} from "@/lib/api";
import type {
  Follow,
  NotificationItem,
  NotificationPreference,
  Team,
} from "@/lib/types";

function teamLabel(team: Team): string {
  return team.city ? `${team.city} ${team.name}` : team.name;
}

const ALERT_TOGGLES: {
  key: keyof NotificationPreference;
  label: string;
  description: string;
}[] = [
  {
    key: "notify_game_start",
    label: "Game about to start",
    description: "Followed teams nearing first pitch.",
  },
  {
    key: "notify_wp_threshold",
    label: "Win-probability swings",
    description: "Large WP moves during live games.",
  },
  {
    key: "notify_high_leverage",
    label: "High-leverage situations",
    description: "Late innings with runners on.",
  },
  {
    key: "notify_game_final",
    label: "Game final",
    description: "Final score for followed teams.",
  },
  {
    key: "notify_new_prediction",
    label: "New prediction",
    description: "When a pregame probability is ready.",
  },
];

export default function ProfilePage() {
  const { user } = useAuth();
  const [teams, setTeams] = useState<Team[]>([]);
  const [follows, setFollows] = useState<Follow[]>([]);
  const [prefs, setPrefs] = useState<NotificationPreference | null>(null);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
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
  const unreadCount = useMemo(
    () => notifications.filter((item) => item.read_at == null).length,
    [notifications],
  );

  async function reloadWatchlist() {
    const [nextTeams, nextFollows] = await Promise.all([
      fetchTeams(),
      fetchFollows(),
    ]);
    setTeams(nextTeams);
    setFollows(nextFollows);
  }

  async function reloadNotifications() {
    const [nextPrefs, nextNotifications] = await Promise.all([
      fetchNotificationPreferences(),
      fetchNotifications({ limit: 30 }),
    ]);
    setPrefs(nextPrefs);
    setNotifications(nextNotifications);
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        await Promise.all([reloadWatchlist(), reloadNotifications()]);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : "Failed to load profile settings.",
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

  async function patchPrefs(update: Partial<NotificationPreference>) {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const next = await updateNotificationPreferences(update);
      setPrefs(next);
      setMessage("Notification preferences saved.");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to update notification preferences.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleMarkRead(notificationId: number) {
    setSaving(true);
    setError(null);
    try {
      const updated = await markNotificationRead(notificationId);
      setNotifications((prev) =>
        prev.map((item) => (item.id === updated.id ? updated : item)),
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to mark notification as read.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleMarkAllRead() {
    setSaving(true);
    setError(null);
    try {
      await markAllNotificationsRead();
      setNotifications((prev) =>
        prev.map((item) =>
          item.read_at
            ? item
            : { ...item, read_at: new Date().toISOString() },
        ),
      );
      setMessage("All notifications marked as read.");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Failed to mark notifications as read.",
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
              Account, watchlist, and notification settings.
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

          <div className="space-y-4 rounded-xl border border-border bg-surface p-6">
            <div>
              <h2 className="text-sm font-medium">Watchlist</h2>
              <p className="mt-1 text-sm text-muted">
                Follow teams to prioritize their games and predictions on the
                home schedule.
              </p>
            </div>

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

          <div className="space-y-4 rounded-xl border border-border bg-surface p-6">
            <div>
              <h2 className="text-sm font-medium">Notifications</h2>
              <p className="mt-1 text-sm text-muted">
                Choose channels and alert types. In-app messages appear below;
                email is delivered by the notification worker when SMTP is
                configured.
              </p>
            </div>

            {loading || !prefs ? (
              <p className="text-sm text-muted">Loading preferences…</p>
            ) : (
              <div className="space-y-5">
                <div className="space-y-3">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
                    Channels
                  </h3>
                  <label className="flex items-start gap-3 text-sm">
                    <input
                      type="checkbox"
                      checked={prefs.in_app_enabled}
                      disabled={saving}
                      onChange={(event) =>
                        void patchPrefs({ in_app_enabled: event.target.checked })
                      }
                      className="mt-0.5"
                    />
                    <span>
                      <span className="font-medium">In-app</span>
                      <span className="mt-0.5 block text-muted">
                        Show alerts in your inbox on this profile.
                      </span>
                    </span>
                  </label>
                  <label className="flex items-start gap-3 text-sm">
                    <input
                      type="checkbox"
                      checked={prefs.email_enabled}
                      disabled={saving}
                      onChange={(event) =>
                        void patchPrefs({ email_enabled: event.target.checked })
                      }
                      className="mt-0.5"
                    />
                    <span>
                      <span className="font-medium">Email</span>
                      <span className="mt-0.5 block text-muted">
                        Send the same alerts to {user?.email} (requires SMTP).
                      </span>
                    </span>
                  </label>
                </div>

                <div className="space-y-3">
                  <h3 className="text-xs font-medium uppercase tracking-wide text-muted">
                    Alert types
                  </h3>
                  {ALERT_TOGGLES.map((toggle) => (
                    <label
                      key={toggle.key}
                      className="flex items-start gap-3 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={Boolean(prefs[toggle.key])}
                        disabled={saving}
                        onChange={(event) =>
                          void patchPrefs({
                            [toggle.key]: event.target.checked,
                          })
                        }
                        className="mt-0.5"
                      />
                      <span>
                        <span className="font-medium">{toggle.label}</span>
                        <span className="mt-0.5 block text-muted">
                          {toggle.description}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>

                <label className="flex max-w-xs flex-col gap-1.5 text-sm">
                  <span className="text-muted">WP swing threshold</span>
                  <input
                    type="number"
                    min={0.01}
                    max={0.99}
                    step={0.01}
                    value={prefs.wp_threshold_pct}
                    disabled={saving}
                    onBlur={(event) => {
                      const value = Number(event.target.value);
                      if (
                        Number.isFinite(value) &&
                        value >= 0.01 &&
                        value <= 0.99 &&
                        value !== prefs.wp_threshold_pct
                      ) {
                        void patchPrefs({ wp_threshold_pct: value });
                      }
                    }}
                    onChange={(event) =>
                      setPrefs({
                        ...prefs,
                        wp_threshold_pct: Number(event.target.value),
                      })
                    }
                    className="rounded-lg border border-border bg-background px-3 py-2 outline-none ring-accent/40 transition focus:border-accent focus:ring-2 disabled:opacity-60"
                  />
                  <span className="text-xs text-muted">
                    Fraction (e.g. 0.15 = 15 percentage points).
                  </span>
                </label>
              </div>
            )}
          </div>

          <div className="space-y-4 rounded-xl border border-border bg-surface p-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-sm font-medium">Inbox</h2>
                <p className="mt-1 text-sm text-muted">
                  {unreadCount > 0
                    ? `${unreadCount} unread in-app notification${unreadCount === 1 ? "" : "s"}.`
                    : "No unread in-app notifications."}
                </p>
              </div>
              <button
                type="button"
                disabled={saving || unreadCount === 0}
                onClick={() => void handleMarkAllRead()}
                className="shrink-0 rounded-md border border-border px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/40 hover:text-foreground disabled:opacity-50"
              >
                Mark all read
              </button>
            </div>

            {loading ? (
              <p className="text-sm text-muted">Loading inbox…</p>
            ) : notifications.length === 0 ? (
              <p className="text-sm text-muted">
                Alerts will appear here once notification rules start firing.
              </p>
            ) : (
              <ul className="divide-y divide-border rounded-lg border border-border">
                {notifications.map((item) => {
                  const unread = item.read_at == null;
                  return (
                    <li
                      key={item.id}
                      className="flex flex-col gap-2 px-4 py-3 text-sm sm:flex-row sm:items-start sm:justify-between"
                    >
                      <div className="min-w-0">
                        <p
                          className={`truncate ${unread ? "font-medium" : "text-muted"}`}
                        >
                          {item.title}
                        </p>
                        <p className="mt-0.5 text-muted">{item.body}</p>
                        <p className="mt-1 text-xs text-muted">
                          {new Date(item.created_at).toLocaleString()} ·{" "}
                          {item.alert_type.replace(/_/g, " ")}
                        </p>
                      </div>
                      {unread ? (
                        <button
                          type="button"
                          disabled={saving}
                          onClick={() => void handleMarkRead(item.id)}
                          className="shrink-0 rounded-md border border-border px-3 py-1.5 text-muted transition-colors hover:border-accent/40 hover:text-foreground disabled:opacity-50"
                        >
                          Mark read
                        </button>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </section>
      </AppShell>
    </ProtectedRoute>
  );
}
