import type { LiveConnectionStatus } from "@/lib/types";

const LABELS: Record<LiveConnectionStatus, string> = {
  connecting: "Connecting…",
  connected: "Live",
  reconnecting: "Reconnecting…",
  polling: "Polling",
  offline: "Offline",
};

const DOT: Record<LiveConnectionStatus, string> = {
  connecting: "bg-muted",
  connected: "bg-accent",
  reconnecting: "bg-muted animate-pulse",
  polling: "bg-amber-400",
  offline: "bg-danger",
};

export function LiveConnectionBadge({
  status,
  degraded,
}: {
  status: LiveConnectionStatus;
  degraded?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs">
      <span className="inline-flex items-center gap-2 rounded-md border border-border bg-surface-elevated px-2.5 py-1 text-muted">
        <span className={`h-2 w-2 rounded-full ${DOT[status]}`} aria-hidden />
        {LABELS[status]}
      </span>
      {degraded ? (
        <span className="rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1 text-danger">
          Live data degraded
        </span>
      ) : null}
    </div>
  );
}
