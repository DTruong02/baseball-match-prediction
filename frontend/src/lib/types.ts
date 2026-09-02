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

export interface ModelVersionSummary {
  id: number;
  run_id: string;
}

export interface Prediction {
  id: number;
  game_pk: number;
  home_win_proba: number;
  away_win_proba: number;
  features?: Record<string, number> | null;
  notes?: string | null;
  model_version: ModelVersionSummary;
  created_at: string;
}

export interface CalibrationBucket {
  bin_low: number;
  bin_high: number;
  n: number;
  predicted_mean: number | null;
  actual_rate: number | null;
}

export interface ModelPerformance {
  model_version_id: number;
  run_id: string;
  n_games: number;
  accuracy: number | null;
  roc_auc: number | null;
  log_loss: number | null;
  brier: number | null;
  calibration_buckets: CalibrationBucket[];
}

export interface ModelPerformanceParams {
  season?: number;
  team?: string;
  confidence_band?: string;
  confidence_min?: number;
  confidence_max?: number;
  model_version_id?: number;
}
