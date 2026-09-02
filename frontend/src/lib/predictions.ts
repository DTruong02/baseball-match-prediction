import type { Prediction } from "@/lib/types";

export function formatWinProbability(probability: number): string {
  return `${Math.round(probability * 100)}%`;
}

export function favoredSide(
  prediction: Prediction,
): "home" | "away" | "even" {
  const diff = prediction.home_win_proba - prediction.away_win_proba;
  if (Math.abs(diff) < 0.005) {
    return "even";
  }
  return diff > 0 ? "home" : "away";
}
