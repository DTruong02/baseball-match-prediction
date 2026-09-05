import type { GameEvent } from "@/lib/types";

function halfLabel(event: GameEvent): string {
  const payload = event.payload;
  if (payload.inning == null) {
    return "";
  }
  const half =
    payload.half_inning ??
    (payload.is_top_inning == null
      ? ""
      : payload.is_top_inning
        ? "Top"
        : "Bot");
  const halfText =
    typeof half === "string" && half.length > 0
      ? half.charAt(0).toUpperCase() + half.slice(1)
      : "";
  return halfText ? `${halfText} ${payload.inning}` : `Inn ${payload.inning}`;
}

function eventTitle(event: GameEvent): string {
  return (
    event.payload.event ||
    event.payload.event_type ||
    event.type ||
    "Play"
  );
}

export function EventTimeline({ events }: { events: GameEvent[] }) {
  const newestFirst = [...events].reverse();

  return (
    <section className="rounded-xl border border-border bg-surface p-6">
      <h2 className="text-sm font-medium text-muted">Play-by-play</h2>
      {newestFirst.length === 0 ? (
        <p className="mt-3 text-sm text-muted">
          No play events yet. They appear once the live worker ingests the feed.
        </p>
      ) : (
        <ol className="mt-4 max-h-96 space-y-3 overflow-y-auto pr-1">
          {newestFirst.map((event) => {
            const scoring = Boolean(event.payload.is_scoring_play);
            const score =
              event.payload.away_score != null &&
              event.payload.home_score != null
                ? `${event.payload.away_score}–${event.payload.home_score}`
                : null;
            return (
              <li
                key={event.event_id}
                className={`rounded-lg border px-3 py-2 ${
                  scoring
                    ? "border-accent/40 bg-accent/5"
                    : "border-border bg-surface-elevated"
                }`}
              >
                <div className="flex items-center justify-between gap-3 text-xs text-muted">
                  <span>{halfLabel(event)}</span>
                  <span className="font-medium text-foreground">
                    {eventTitle(event)}
                    {score ? ` · ${score}` : ""}
                  </span>
                </div>
                <p className="mt-1 text-sm leading-snug">
                  {event.payload.description || "No description."}
                </p>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
