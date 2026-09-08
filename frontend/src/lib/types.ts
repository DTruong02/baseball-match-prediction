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

export interface Follow {
  id: number;
  entity_type: "team" | "player" | string;
  team: Team | null;
  player: Player | null;
  created_at: string;
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
  followed?: boolean;
  pregame_prediction?: Prediction | null;
  live_prediction?: Prediction | null;
}

export interface ApiErrorBody {
  detail?: string | { msg: string }[];
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

export interface LiveState {
  game_pk: number;
  home_score: number;
  away_score: number;
  status: string;
  detailed_state: string;
  current_inning: number | null;
  inning_state: string | null;
  is_top_inning: boolean | null;
  outs: number | null;
  balls: number | null;
  strikes: number | null;
  events_inserted: number;
  updated_at: string | null;
  pitcher_id?: number | null;
  home_win_proba?: number | null;
  away_win_proba?: number | null;
  model_version_id?: number | null;
  model_run_id?: string | null;
  wp_explanation?: string | null;
  wp_delta_home?: number | null;
}

export interface LiveSnapshot {
  data: LiveState | null;
  source: "redis" | "postgres" | null;
  degraded: boolean;
}

export interface GameEventPayload {
  inning?: number | null;
  half_inning?: string | null;
  is_top_inning?: boolean | null;
  is_scoring_play?: boolean | null;
  is_complete?: boolean | null;
  event?: string | null;
  event_type?: string | null;
  description?: string | null;
  rbi?: number | null;
  away_score?: number | null;
  home_score?: number | null;
  batter_name?: string | null;
  pitcher_name?: string | null;
  [key: string]: unknown;
}

export interface GameEvent {
  id: number;
  game_pk: number;
  event_id: string;
  type: string;
  payload: GameEventPayload;
  sequence: number;
  ingested_at: string;
}

export type LiveConnectionStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "polling"
  | "offline";

export interface LiveWsMessage {
  type: "snapshot" | "update" | "slate_snapshot";
  game_pk?: number;
  date?: string;
  data: LiveState | LiveWsMessage[] | null;
  source: "redis" | "postgres" | null;
  degraded: boolean;
}

export interface NotificationPreference {
  in_app_enabled: boolean;
  email_enabled: boolean;
  notify_game_start: boolean;
  notify_wp_threshold: boolean;
  notify_high_leverage: boolean;
  notify_game_final: boolean;
  notify_new_prediction: boolean;
  wp_threshold_pct: number;
}

export type NotificationPreferenceUpdate = Partial<NotificationPreference>;

export interface NotificationItem {
  id: number;
  channel: string;
  alert_type: string;
  title: string;
  body: string;
  payload?: Record<string, unknown> | null;
  status: string;
  read_at: string | null;
  delivered_at: string | null;
  created_at: string;
}

export interface NotificationUnreadCount {
  count: number;
}
