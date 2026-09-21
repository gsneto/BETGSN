/**
 * Contratos do modulo de BACKTEST — espelho de
 * `betgsn/api/backtest_schemas.py`.
 *
 * Convencoes identicas ao resto da API: fracoes em [0,1], dinheiro na
 * moeda da banca. O frontend so formata.
 */

export type OutcomeResult = "win" | "loss" | "push";

export type JobPhase =
  | "idle"
  | "preparing"
  | "analyzing"
  | "metrics"
  | "saving"
  | "done"
  | "error";

export interface BacktestRequest {
  start_date: string | null;
  end_date: string | null;
  competitions: string[];
  market_keys: string[];
  bankroll: number;
  kelly_fraction: number;
  stake_cap: number;
  min_ev: number;
  max_exposure: number;
  use_xg: boolean;
  min_confidence: "FORTE" | "MEDIA" | "FRACA" | "DESCARTE";
  min_history: number;
  odds_source: "naive_synthetic" | "real_historical" | "football_data_uk";
  apply_exposure_cap: boolean;
  /** corpus histórico disponível */
  corpus_source: "local" | "imported" | "football_data_uk";
  /** chave de esporte da The Odds API quando odds_source = real_historical */
  odds_sport_key: string;
  /** football-data.co.uk: usar linha de fechamento (true) ou abertura (false) */
  odds_closing: boolean;
  /** football-data.co.uk: restringir a estes bookmakers (vazio = todos) */
  odds_books: string[];
}

export interface CorpusOption {
  key: string;
  label: string;
  available: boolean;
  n_matches: number;
  first_kickoff: string | null;
  last_kickoff: string | null;
  competitions: string[];
  fingerprint: string | null;
  note: string;
}

export interface OddsCacheStatus {
  sport_key: string;
  snapshots: number;
  first: string | null;
  last: string | null;
}

export interface MarketOption {
  key: string;
  label: string;
}

export interface OddsSourceOption {
  key: string;
  label: string;
  available: boolean;
  note: string;
}

export interface MissingDataNote {
  dado: string;
  por_que: string;
  quem_fornece: string;
  como_plugar: string;
}

export interface BacktestOptions {
  min_date: string;
  max_date: string;
  competitions: string[];
  markets: MarketOption[];
  default_config: BacktestRequest;
  min_history_default: number;
  odds_sources: OddsSourceOption[];
  scanner_ev_thresholds: Record<string, number>;
  min_sample: number;
  point_in_time_schema: string;
  model_version: string;
  missing_data_notes: MissingDataNote[];
  /** corpora disponíveis (local sintético + temporadas importadas + fduk) */
  corpora: CorpusOption[];
  /** estado do cache de odds históricas reais */
  odds_cache: OddsCacheStatus[];
  /** bookmakers disponíveis na fonte football-data.co.uk */
  books: string[];
}

export interface BacktestJobStatus {
  job_id: string | null;
  phase: JobPhase;
  done: number;
  total: number;
  progress: number;
  message: string;
  run_id: string | null;
  error: string | null;
}

export interface AggregateMetrics {
  n_signals: number;
  n_settled: number;
  n_unsettled: number;
  n_wins: number;
  n_losses: number;
  n_pushes: number;
  hit_rate: number;
  hit_rate_ci: [number, number] | number[];
  avg_odd: number;
  avg_model_prob: number;
  avg_market_prob: number;
  avg_edge: number;
  avg_ev: number;
  avg_realized_return: number;
  realized_return_ci: [number, number] | number[];
  brier: number;
  brier_ci: [number, number] | number[];
  logloss: number;
  ev_gap: number;
  sample_sufficient: boolean;
}

export interface CalibrationBin {
  label: string;
  lower: number;
  upper: number;
  n: number;
  avg_predicted: number;
  observed_rate: number;
  ci_low: number;
  ci_high: number;
  sufficient: boolean;
}

export interface EvBucket {
  label: string;
  lower: number;
  upper: number;
  n: number;
  avg_ev: number;
  observed_rate: number;
  avg_realized_return: number;
  ci_low: number;
  ci_high: number;
  sufficient: boolean;
}

export interface TemporalBucket {
  label: string;
  n: number;
  hit_rate: number;
  avg_ev: number;
  realized_return: number;
  profit: number;
  bankroll_end: number | null;
}

export interface SegmentRow {
  dimension: string;
  segment: string;
  n: number;
  observed_rate: number;
  avg_predicted: number;
  avg_ev: number;
  brier: number;
  ci_low: number;
  ci_high: number;
  sufficient: boolean;
}

export interface EquityPoint {
  day: string;
  bankroll: number;
  peak: number;
  drawdown: number;
  staked: number;
  profit: number;
  n_signals: number;
}

export interface Simulation {
  initial_bankroll: number;
  final_bankroll: number;
  profit: number;
  return_pct: number;
  total_staked: number;
  roi: number;
  max_drawdown: number;
  max_drawdown_day: string;
  longest_win_streak: number;
  longest_loss_streak: number;
  n_bets: number;
  n_wins: number;
  n_losses: number;
  n_pushes: number;
  return_std: number;
  equity: EquityPoint[];
  exposure_scaled_days: number;
}

export interface BacktestSignal {
  signal_id: string;
  match_id: string;
  competition: string;
  season: string;
  kickoff: string;
  kickoff_utc: string;
  home: string;
  away: string;
  market: string;
  outcome: string;
  best_odd: number;
  best_book: string;
  median_odd: number;
  fair_odd: number;
  n_books: number;
  model_prob: number;
  market_prob: number;
  edge: number;
  ev: number;
  kelly: number;
  stake: number;
  stake_pct: number;
  expected_profit: number;
  confidence: string;
  rationale: string;
  lambda_home: number;
  lambda_away: number;
  home_attack: number;
  home_defense: number;
  away_attack: number;
  away_defense: number;
  home_xg_for: number;
  away_xg_for: number;
  n_prior_matches: number;
  league_goals: number;
  attack_blend: number;
  odds_source: string;
  odds_as_of: string;
  model_version: string;
  config_hash: string;
  result_home_goals: number;
  result_away_goals: number;
  outcome_result: OutcomeResult | null;
  settled: boolean;
  realized_return: number | null;
  profit: number | null;
}

export interface SignalPage {
  total: number;
  offset: number;
  limit: number;
  items: BacktestSignal[];
}

export interface BacktestRunDetail {
  run_id: string;
  created_at: string;
  config_hash: string;
  model_version: string;
  point_in_time_schema: string;
  config: BacktestRequest;
  aggregate: AggregateMetrics;
  calibration: CalibrationBin[];
  ev_buckets: EvBucket[];
  temporal: Record<"day" | "week" | "month", TemporalBucket[]>;
  segments: SegmentRow[];
  simulation: Simulation;
  meta: {
    started_at?: string;
    finished_at?: string;
    n_matches_in_period?: number;
    skipped_reasons?: Record<string, number>;
    duration_ms?: number;
  };
  missing_data_notes: MissingDataNote[];
}

export interface BacktestRunSummary {
  run_id: string;
  created_at: string;
  config_hash: string;
  model_version: string;
  n_signals: number;
  n_matches_evaluated: number;
  n_matches_skipped: number;
  duration_ms: number;
  hit_rate: number | null;
  brier: number | null;
  return_pct: number | null;
  max_drawdown: number | null;
}

export interface MetricDelta {
  a: number;
  b: number;
  delta: number;
}

export interface TemporalComparison {
  label: string;
  hit_rate_a: number;
  hit_rate_b: number;
  n_a: number;
  n_b: number;
}

export interface RunComparison {
  run_a: { run_id: string; created_at: string; config_hash: string };
  run_b: { run_id: string; created_at: string; config_hash: string };
  same_config: boolean;
  metrics: Record<string, MetricDelta>;
  temporal_month: TemporalComparison[];
}

export type TemporalGranularity = "day" | "week" | "month";
