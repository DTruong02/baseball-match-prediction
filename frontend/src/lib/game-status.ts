import type { Game } from "@/lib/types";

/** Detailed states the live worker treats as in-progress (mirrors backend). */
const LIVE_DETAILED_STATES = new Set([
  "In Progress",
  "Delayed",
  "Delayed Start",
  "Manager Challenge",
  "Suspended",
  "Warmup",
]);

export function isInProgressGame(game: Pick<Game, "status" | "detailed_state">): boolean {
  if (game.status === "Live") {
    return true;
  }
  return LIVE_DETAILED_STATES.has(game.detailed_state);
}
