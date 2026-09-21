/**
 * Contratos da API BETGSN — espelho dos modelos Pydantic em
 * `betgsn/api/schemas.py`. Se um campo mudar no backend, muda aqui.
 *
 * CONVENCOES (identicas ao backend):
 *  - probabilidades: fracao em [0, 1];
 *  - edge: diferenca de probabilidade em fracao (0.0776 = +7.76pp);
 *  - ev e percentuais: fracao (0.1163 = +11.63%);
 *  - dinheiro: numero na moeda da banca.
 *
 * O frontend apenas FORMATA estes valores. Nenhum calculo estatistico
 * (Poisson, Dixon-Coles, consenso, Kelly, edge, EV) acontece no cliente.
 */

export type ConfidenceLevel = "FORTE" | "MEDIA" | "FRACA" | "DESCARTE";

export interface ModelConfiguration {
  bankroll: number;
  kelly_fraction: number;
  min_ev: number;
  stake_cap: number;
  max_exposure: number;
  use_xg: boolean;
  rounds: number;
}

export interface ModelConstants {
  rho_dixon_coles: number;
  ev_forte: number;
  ev_media: number;
  ev_fraca: number;
  min_books: number;
  max_spread: number;
  max_goals_grid: number;
  league_avg_goals: number;
  home_advantage: number;
  attack_blend: number;
  dataset_seed: number;
  bookmakers: string[];
}

/* ------------------------------------------------------------------ sinais */

export interface Signal {
  id: string;
  match: string;
  home: string;
  away: string;
  kickoff: string;
  league: string;
  round_label: string;
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
  expected_profit_pct: number;
  gross_profit_if_win: number;
  loss_if_lose: number;
  confidence: ConfidenceLevel;
  rationale: string;
}

export interface SignalsKpis {
  total: number;
  strong: number;
  medium: number;
  weak: number;
  strong_pct: number;
  medium_pct: number;
  weak_pct: number;
  expected_profit: number;
  expected_profit_pct: number;
  avg_stake_pct: number;
  total_exposure: number;
  total_exposure_pct: number;
  worst_case_loss: number;
  gross_profit_if_all_win: number;
  exposure_scaled_by: number;
  max_ev: number;
}

export interface ModelCalibrationInfo {
  measured_on: string;
  ev_predicted: number;
  return_realized: number;
  gap_pp: number;
  simulated_roi: number;
  verdict: string;
}

/** De onde vieram os sinais. */
export type SignalSource = "synthetic" | "real";

export interface SignalReport {
  generated_at: string;
  bankroll: number;
  kpis: SignalsKpis;
  signals: Signal[];
  top_tips: string[];
  /** synthetic = dataset gerado em memoria; real = jogos futuros reais */
  source: SignalSource;
  source_detail: string;
  /** jogos futuros descartados por falta de rating do time */
  skipped_no_rating: number;
  /** jogos futuros com odds mas sem consenso minimo de casas */
  skipped_insufficient_books: number;
  /** presente apenas quando source = "real" */
  calibration: ModelCalibrationInfo | null;
}

export interface SignalsSourceStatus {
  real: {
    available: boolean;
    n_fixtures: number;
    with_odds?: number;
    competitions?: string[];
    first_date?: string | null;
    last_date?: string | null;
    error?: string;
  };
  synthetic: { available: boolean };
  default_source: SignalSource;
}

/* ------------------------------------------------------------------- jogos */

export interface Scoreline {
  home_goals: number;
  away_goals: number;
  prob: number;
}

export interface MarketProbabilities {
  market: string;
  outcomes: Record<string, number>;
}

export interface TeamSnapshot {
  name: string;
  attack: number;
  defense: number;
  strength: number;
  goals_for: number;
  goals_against: number;
  xg_for: number | null;
  xg_against: number | null;
  xg_status?: "REAL" | "ESTIMATED" | "UNAVAILABLE";
  xg_source?: string | null;
  corners_for: number;
  corners_against: number;
  cards_for: number;
  cards_against: number;
  shots_for: number;
  shots_on_target_for: number;
  form_points: number;
  matches_played: number;
}

export interface GameAnalysis {
  id: string;
  match: string;
  home: string;
  away: string;
  league: string;
  kickoff: string;
  round_label: string;
  lambda_home: number;
  lambda_away: number;
  prob_home: number;
  prob_draw: number;
  prob_away: number;
  prob_over_25: number;
  prob_btts: number;
  prob_home_corners_over_55: number;
  prob_cards_over_35: number;
  top_scorelines: Scoreline[];
  markets: MarketProbabilities[];
  ratings_home: TeamSnapshot;
  ratings_away: TeamSnapshot;
  n_markets_with_odds: number;
  signal_count: number;
}

/* ------------------------------------------------------------- casas/odds */

export interface BookmakerRow {
  book: string;
  odds: Record<string, number>;
  best_outcomes: string[];
  margin: number;
}

export interface ArbLeg {
  outcome: string;
  book: string;
  odd: number;
  stake: number;
  payout: number;
}

export interface ArbitrageCheck {
  arbitrage: boolean;
  margin: number;
  legs: ArbLeg[];
}

export interface MarketComparison {
  match: string;
  market: string;
  outcomes: string[];
  rows: BookmakerRow[];
  best_odds: Record<string, number>;
  best_books: Record<string, string>;
  model_probs: Record<string, number>;
  market_probs: Record<string, number>;
  arbitrage: ArbitrageCheck;
}

export interface BookmakerSnapshot {
  book: string;
  n_markets: number;
  n_best_odds: number;
  avg_margin: number;
  best_odd_share: number;
  signals_won: number;
}

export interface OddsOverview {
  generated_at: string;
  matches: string[];
  markets_by_match: Record<string, string[]>;
  bookmakers: BookmakerSnapshot[];
}

/* ------------------------------------------------------ estatisticas/modelo */

export interface MarketBreakdown {
  market: string;
  signals: number;
  avg_ev: number;
  avg_edge: number;
  best_ev: number;
  total_stake: number;
  expected_profit: number;
}

export interface ConfidenceBreakdown {
  confidence: ConfidenceLevel;
  signals: number;
  avg_ev: number;
  avg_odd: number;
  total_stake: number;
  expected_profit: number;
}

export interface BookBreakdown {
  book: string;
  signals: number;
  avg_ev: number;
  avg_odd: number;
  total_stake: number;
}

export interface EvBucket {
  label: string;
  lower: number;
  upper: number;
  count: number;
}

export interface StatsOverview {
  generated_at: string;
  league_goals: number;
  home_advantage: number;
  teams: TeamSnapshot[];
  n_history_matches: number;
  n_fixtures: number;
  by_market: MarketBreakdown[];
  by_confidence: ConfidenceBreakdown[];
  by_book: BookBreakdown[];
  ev_distribution: EvBucket[];
}

export interface CalibrationBin {
  predicted: number;
  empirical: number;
  n: number;
}

export interface ModelPerformance {
  split: number;
  n_train: number;
  n_test: number;
  logloss: number;
  brier: number;
  accuracy: number;
  calibration_bins: CalibrationBin[];
  bankroll_start: number;
  bankroll_end: number;
  n_bets: number;
  n_wins: number;
  hit_rate: number;
  total_staked: number;
  profit: number;
  roi: number;
  ev_mean_pred: number;
  return_mean_real: number;
  max_drawdown: number;
  summary: string;
}

export interface ProbabilityModel {
  version: string;
  engine: string;
  generated_at: string;
  constants: ModelConstants;
  configuration: ModelConfiguration;
  league_goals: number;
  home_advantage: number;
  attack_blend: number;
  n_teams: number;
  n_history_matches: number;
  n_fixtures: number;
  n_markets: number;
  markets: string[];
  data_source: string;
  providers: Record<string, boolean>;
  documentation: string;
}

/* ------------------------------------------------------- dashboard/status */

export interface Provenance {
  source: string;
  model_version: string;
  data_version: string | null;
  prediction_timestamp: string | null;
  odds_timestamp: string | null;
  calibration_status: string;
  calibration_version: string | null;
  xg_status: "REAL" | "ESTIMATED" | "UNAVAILABLE";
  xg_source: string | null;
}

export interface DashboardSummary {
  provenance?: Provenance;
  generated_at: string;
  computed_in_ms: number;
  configuration: ModelConfiguration;
  kpis: SignalsKpis;
  n_games: number;
  n_teams: number;
  n_bookmakers: number;
  n_markets: number;
  data_source: string;
}

export interface SystemStatus {
  status: "ok" | "computing" | "error";
  version: string;
  python_version: string;
  generated_at: string | null;
  computed_in_ms: number | null;
  has_snapshot: boolean;
  n_signals: number;
  n_games: number;
  data_source: string;
  providers: Record<string, boolean>;
  message: string | null;
}

export interface ApiErrorBody {
  error: string;
  detail: string;
  hint?: string | null;
}

/* ------------------------------------------------------------- UI locais */

export type TabKey =
  | "signals"
  | "games"
  | "odds"
  | "stats"
  | "model"
  | "backtest"
  | "portfolio"
  | "corners"
  | "cards";

export type ConfidenceFilter = "all" | "FORTE" | "MEDIA" | "FRACA";

export interface WsMessage<T = unknown> {
  event:
    | "status"
    | "pong"
    | "recalculate:start"
    | "recalculate:done"
    | "recalculate:error";
  payload: T;
}
