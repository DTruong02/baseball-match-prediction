"use client";

import { AppShell } from "@/components/AppShell";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { useAuth } from "@/contexts/AuthContext";

export default function ProfilePage() {
  const { user } = useAuth();

  return (
    <ProtectedRoute>
      <AppShell>
        <section className="space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
            <p className="mt-1 text-sm text-muted">
              Account settings and preferences.
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

          <div className="rounded-xl border border-dashed border-border bg-surface/60 p-6">
            <h2 className="text-sm font-medium">Settings</h2>
            <p className="mt-2 text-sm text-muted">
              Notification preferences, followed teams, and personalization
              controls will be added in a later stage.
            </p>
          </div>
        </section>
      </AppShell>
    </ProtectedRoute>
  );
}
