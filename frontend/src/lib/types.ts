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

export interface TeamFangraphsStats {
  fangraphs_team: string;
  wrc_plus: number | null;
  team_fip: number | null;
  bullpen_fip: number | null;
  median_starter_fip: number | null;
}

export interface PitcherFangraphsStats {
  fangraphs_name: string;
  fangraphs_team: string | null;
  fip: number | null;
  era: number | null;
  ip: number | null;
  gs: number | null;
  k_per_9: number | null;
  bb_per_9: number | null;
}

export interface MonthlyTrendRow {
  month: string;
  games: number;
  wins: number;
  losses: number;
  runs_scored: number;
  runs_allowed: number;
  run_diff: number;
}

export interface TeamAnalytics {
  team: Team;
  season: number;
  record: {
    games: number;
    wins: number;
    losses: number;
    runs_scored: number;
    runs_allowed: number;
    run_diff: number;
  };
  splits: {
    home: { games: number; wins: number; losses: number };
    away: { games: number; wins: number; losses: number };
  };
  monthly_trend: MonthlyTrendRow[];
  prediction_accuracy: {
    n_predictions: number;
    n_correct: number;
    accuracy: number | null;
    model_version_id?: number | null;
    run_id?: string | null;
  };
  fangraphs: TeamFangraphsStats | null;
  recent_games: {
    game_pk: number;
    game_date: string;
    opponent_abbreviation: string;
    opponent_name: string;
    is_home: boolean;
    runs_scored: number;
    runs_allowed: number;
    result: string;
  }[];
}

export interface PlayerDetail {
  id: number;
  full_name: string;
  primary_position: string | null;
  team: Team | null;
}

export interface PlayerAnalytics {
  player: PlayerDetail;
  season: number;
  probable_starts: {
    games: number;
    wins: number;
    losses: number;
    win_pct: number | null;
    games_detail: {
      game_pk: number;
      game_date: string;
      is_home: boolean;
      opponent_abbreviation: string;
      opponent_name: string;
      team_score: number | null;
      opponent_score: number | null;
      result: string | null;
      detailed_state: string;
    }[];
  };
  event_splits: {
    scoring_plays_as_batter: number;
    scoring_plays_as_pitcher: number;
    rbi: number;
    events_scanned: number;
  };
  fangraphs: PitcherFangraphsStats | null;
}

export interface MatchupAnalytics {
  season: number;
  home_team: Team;
  away_team: Team;
  head_to_head: {
    meetings: number;
    scored_games: number;
    home_wins: number;
    away_wins: number;
    games: {
      game_pk: number;
      game_date: string;
      venue_home_abbreviation: string;
      venue_away_abbreviation: string;
      home_score: number | null;
      away_score: number | null;
      detailed_state: string;
      winner: string | null;
    }[];
  };
  fangraphs_home: TeamFangraphsStats | null;
  fangraphs_away: TeamFangraphsStats | null;
  fangraphs_diff: {
    wrc_plus: number | null;
    team_fip: number | null;
    bullpen_fip: number | null;
    median_starter_fip: number | null;
  } | null;
}

export interface AiExplainResponse {
  game_pk: number;
  explanation: string;
  home_win_proba: number | null;
  away_win_proba: number | null;
  features?: Record<string, number> | null;
  notes?: string | null;
  model_version: ModelVersionSummary;
}

export interface AiSummarizeResponse {
  game_pk: number;
  summary: string;
}

export interface AiAskResponse {
  answer: string;
  tool_trace: Record<string, unknown>[];
  game_pk?: number | null;
  date?: string | null;
}
