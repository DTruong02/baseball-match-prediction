export interface User {
  id: number;
  email: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface Team {
  id: number;
  abbreviation: string;
  name: string;
  city: string | null;
}

export interface Player {
  id: number;
  full_name: string;
}

export interface Game {
  id: number;
  game_pk: number;
  game_date: string;
  season: number;
  status: string;
  detailed_state: string;
  home_team: Team;
  away_team: Team;
  venue_id: number | null;
  venue_name: string | null;
  home_probable_pitcher: Player | null;
  away_probable_pitcher: Player | null;
  home_score: number | null;
  away_score: number | null;
  winner: string | null;
}

export interface ApiErrorBody {
  detail?: string | { msg: string }[];
}
