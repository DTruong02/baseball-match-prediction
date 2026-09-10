import type { Team } from "@/lib/types";

/** Format branded ``Geo Nickname`` (e.g. Arizona Diamondbacks) without doubling. */
export function teamLabel(team: Pick<Team, "name" | "city">): string {
  const name = (team.name || "").trim();
  const city = (team.city || "").trim();
  if (!city) {
    return name;
  }
  if (!name) {
    return city;
  }
  if (name.toLowerCase().startsWith(`${city.toLowerCase()} `)) {
    return name;
  }
  return `${city} ${name}`;
}
