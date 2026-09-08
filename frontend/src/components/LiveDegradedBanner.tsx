/** Full-width banner when live feed / Redis is degraded. */

export function LiveDegradedBanner({
  show,
  detail,
}: {
  show: boolean;
  detail?: string;
}) {
  if (!show) {
    return null;
  }

  return (
    <div
      role="status"
      className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger"
    >
      <p className="font-medium">Live data degraded</p>
      <p className="mt-1 text-danger/90">
        {detail ??
          "Showing the last known snapshot. Scores and win probability may lag until the live feed recovers."}
      </p>
    </div>
  );
}
