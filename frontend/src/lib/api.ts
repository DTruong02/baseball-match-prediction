import {
  clearStoredToken,
  getStoredToken,
  setStoredToken,
} from "@/lib/auth-storage";
import type { ApiErrorBody, Game, TokenResponse, User } from "@/lib/types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ??
  "http://localhost:8000";

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

export async function fetchGames(date: string): Promise<Game[]> {
  return apiFetch<Game[]>(`/games?date=${encodeURIComponent(date)}`, {}, true);
}

export async function fetchGame(gamePk: number): Promise<Game> {
  return apiFetch<Game>(`/games/${gamePk}`, {}, true);
}

export async function fetchPrediction(
  gamePk: number,
): Promise<null | Record<string, unknown>> {
  return apiFetch<null | Record<string, unknown>>(
    `/predictions/${gamePk}`,
    {},
    true,
  );
}

export function logout(): void {
  clearStoredToken();
}
