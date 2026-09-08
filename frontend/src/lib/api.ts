import {
  clearStoredToken,
  getStoredToken,
  setStoredToken,
} from "@/lib/auth-storage";
import type {
  AiAskResponse,
  AiExplainResponse,
  AiSummarizeResponse,
  ApiErrorBody,
  Follow,
  Game,
  GameEvent,
  LiveSnapshot,
  MatchupAnalytics,
  ModelPerformance,
  ModelPerformanceParams,
  NotificationItem,
  NotificationPreference,
  NotificationPreferenceUpdate,
  NotificationUnreadCount,
  PlayerAnalytics,
  Prediction,
  Team,
  TeamAnalytics,
  TokenResponse,
  User,
} from "@/lib/types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ??
  "http://localhost:8000";

export function getApiBaseUrl(): string {
  return API_BASE;
}

export function liveGameWebSocketUrl(gamePk: number, token: string): string {
  const wsBase = API_BASE.replace(/^http/, "ws");
  return `${wsBase}/ws/games/${gamePk}?token=${encodeURIComponent(token)}`;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function formatErrorDetail(body: ApiErrorBody): string {
  if (!body.detail) {
    return "Request failed";
  }
  if (typeof body.detail === "string") {
    return body.detail;
  }
  return body.detail.map((item) => item.msg).join(", ");
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) {
      return undefined as T;
    }
    const text = await response.text();
    if (!text) {
      return undefined as T;
    }
    return JSON.parse(text) as T;
  }

  let message = response.statusText;
  try {
    const body = (await response.json()) as ApiErrorBody;
    message = formatErrorDetail(body);
  } catch {
    // keep status text
  }
  throw new ApiError(message, response.status);
}

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
  authenticated = false,
): Promise<T> {
  const headers = new Headers(options.headers);

  if (authenticated) {
    const token = getStoredToken();
    if (!token) {
      throw new ApiError("Not authenticated", 401);
    }
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  return parseResponse<T>(response);
}

export async function login(email: string, password: string): Promise<void> {
  const body = new URLSearchParams({
    username: email,
    password,
  });

  const token = await apiFetch<TokenResponse>("/auth/login", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });

  setStoredToken(token.access_token);
}

export async function register(email: string, password: string): Promise<User> {
  return apiFetch<User>("/auth/register", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, password }),
  });
}

export async function fetchCurrentUser(): Promise<User> {
  return apiFetch<User>("/auth/me", {}, true);
}

export async function fetchGames(
  date: string,
  options: { followingOnly?: boolean } = {},
): Promise<Game[]> {
  const search = new URLSearchParams({ date });
  if (options.followingOnly) {
    search.set("following_only", "true");
  }
  return apiFetch<Game[]>(`/games?${search.toString()}`, {}, true);
}

export async function fetchTeams(): Promise<Team[]> {
  return apiFetch<Team[]>("/teams", {}, true);
}

export async function fetchFollows(): Promise<Follow[]> {
  return apiFetch<Follow[]>("/follows", {}, true);
}

export async function followTeam(teamId: number): Promise<Follow> {
  return apiFetch<Follow>(
    "/follows",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_type: "team", team_id: teamId }),
    },
    true,
  );
}

export async function followPlayer(playerId: number): Promise<Follow> {
  return apiFetch<Follow>(
    "/follows",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_type: "player", player_id: playerId }),
    },
    true,
  );
}

export async function unfollow(followId: number): Promise<void> {
  return apiFetch<void>(`/follows/${followId}`, { method: "DELETE" }, true);
}

export async function fetchNotificationPreferences(): Promise<NotificationPreference> {
  return apiFetch<NotificationPreference>(
    "/notifications/preferences",
    {},
    true,
  );
}

export async function updateNotificationPreferences(
  body: NotificationPreferenceUpdate,
): Promise<NotificationPreference> {
  return apiFetch<NotificationPreference>(
    "/notifications/preferences",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    true,
  );
}

export async function fetchNotifications(
  options: { unreadOnly?: boolean; limit?: number } = {},
): Promise<NotificationItem[]> {
  const search = new URLSearchParams();
  if (options.unreadOnly) {
    search.set("unread_only", "true");
  }
  if (options.limit != null) {
    search.set("limit", String(options.limit));
  }
  const query = search.toString();
  const path = query ? `/notifications?${query}` : "/notifications";
  return apiFetch<NotificationItem[]>(path, {}, true);
}

export async function fetchNotificationUnreadCount(): Promise<NotificationUnreadCount> {
  return apiFetch<NotificationUnreadCount>(
    "/notifications/unread-count",
    {},
    true,
  );
}

export async function markNotificationRead(
  notificationId: number,
): Promise<NotificationItem> {
  return apiFetch<NotificationItem>(
    `/notifications/${notificationId}/read`,
    { method: "POST" },
    true,
  );
}

export async function markAllNotificationsRead(): Promise<NotificationUnreadCount> {
  return apiFetch<NotificationUnreadCount>(
    "/notifications/read-all",
    { method: "POST" },
    true,
  );
}

export async function fetchGame(gamePk: number): Promise<Game> {
  return apiFetch<Game>(`/games/${gamePk}`, {}, true);
}

export async function fetchPrediction(
  gamePk: number,
): Promise<Prediction | null> {
  return apiFetch<Prediction | null>(`/predictions/${gamePk}`, {}, true);
}

export async function fetchLiveSnapshot(gamePk: number): Promise<LiveSnapshot> {
  return apiFetch<LiveSnapshot>(`/games/${gamePk}/live`, {}, true);
}

export async function fetchGameEvents(
  gamePk: number,
  limit = 100,
): Promise<GameEvent[]> {
  return apiFetch<GameEvent[]>(
    `/games/${gamePk}/events?limit=${limit}`,
    {},
    true,
  );
}

export async function fetchModelPerformance(
  params: ModelPerformanceParams = {},
): Promise<ModelPerformance> {
  const search = new URLSearchParams();
  if (params.season != null) {
    search.set("season", String(params.season));
  }
  if (params.team) {
    search.set("team", params.team);
  }
  if (params.confidence_band) {
    search.set("confidence_band", params.confidence_band);
  }
  if (params.confidence_min != null) {
    search.set("confidence_min", String(params.confidence_min));
  }
  if (params.confidence_max != null) {
    search.set("confidence_max", String(params.confidence_max));
  }
  if (params.model_version_id != null) {
    search.set("model_version_id", String(params.model_version_id));
  }
  const query = search.toString();
  const path = query ? `/model/performance?${query}` : "/model/performance";
  return apiFetch<ModelPerformance>(path, {}, true);
}

export async function fetchTeamAnalytics(
  teamId: number,
  season: number,
): Promise<TeamAnalytics> {
  return apiFetch<TeamAnalytics>(
    `/teams/${teamId}/analytics?season=${season}`,
    {},
    true,
  );
}

export async function fetchPlayerAnalytics(
  playerId: number,
  season: number,
): Promise<PlayerAnalytics> {
  return apiFetch<PlayerAnalytics>(
    `/players/${playerId}/analytics?season=${season}`,
    {},
    true,
  );
}

export async function fetchMatchupAnalytics(
  homeTeamId: number,
  awayTeamId: number,
  season: number,
): Promise<MatchupAnalytics> {
  const search = new URLSearchParams({
    home_team_id: String(homeTeamId),
    away_team_id: String(awayTeamId),
    season: String(season),
  });
  return apiFetch<MatchupAnalytics>(
    `/analytics/matchup?${search.toString()}`,
    {},
    true,
  );
}

export async function explainGameLean(
  gamePk: number,
): Promise<AiExplainResponse> {
  return apiFetch<AiExplainResponse>(
    "/ai/explain",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_pk: gamePk }),
    },
    true,
  );
}

export async function summarizeGame(
  gamePk: number,
): Promise<AiSummarizeResponse> {
  return apiFetch<AiSummarizeResponse>(
    "/ai/summarize-game",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_pk: gamePk }),
    },
    true,
  );
}

export async function askAi(options: {
  question: string;
  gamePk?: number;
  date?: string;
}): Promise<AiAskResponse> {
  return apiFetch<AiAskResponse>(
    "/ai/ask",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: options.question,
        game_pk: options.gamePk,
        date: options.date,
      }),
    },
    true,
  );
}

export function logout(): void {
  clearStoredToken();
}
