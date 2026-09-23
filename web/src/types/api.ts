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

// ------------------------------------------------------------------ status ---

// Vocabulario canonico do dominio (odds_health.ProviderState) + UNKNOWN
// para provider sem observacao registrada. "CURRENT" nao existe.
export type ProviderAvailability =
  | "HEALTHY"
  | "DEGRADED"
  | "UNAVAILABLE"
  | "STALE"
  | "NO_COVERAGE"
  | "UNKNOWN";

export interface ProviderHealth {
  name: string;
  status: ProviderAvailability;
  last_update: string | null;
  last_execution: string | null;
  latency_ms: number | null;
  error: string | null;
  quota_used: number | null;
  quota_remaining: number | null;
  coverage: Record<string, boolean>;
  features: string[];
  message: string | null;
}

export interface ProviderOverview {
  providers: ProviderHealth[];
  generated_at: string;
  any_healthy: boolean;
  any_stale: boolean;
  any_unavailable: boolean;
}

// ------------------------------------------------------------------ fixtures ---

export interface FixtureItem {
  match: string;
  home: string;
  away: string;
  league: string;
  round_label: string;
  /** Instante do kickoff em UTC canônico (YYYY-MM-DDTHH:MM:SSZ). */
  kickoff: string;
  /** Horário local da competição (YYYY-MM-DD HH:MM), quando disponível. */
  kickoff_local?: string;
  /** Fuso IANA de origem (ex.: "Europe/London"), quando disponível. */
  timezone?: string;
  has_odds: boolean;
  n_bookmakers: number;
  bookmakers: string[];
  markets: string[];
  best_odds: Record<string, number> | null;
  status: "UPCOMING" | "LIVE" | "SETTLED" | "NO_ODDS";
}

export interface FixtureOverview {
  generated_at: string;
  n_fixtures: number;
  n_with_odds: number;
  fixtures: FixtureItem[];
  source: string;
  data_version: string | null;
}

// ------------------------------------------------------------ odds movement ---

export interface PricePoint {
  bookmaker: string;
  market: string;
  outcome: string;
  odd: number;
  timestamp: string;
  is_opening: boolean;
  is_closing: boolean;
}

export interface OddsMovement {
  match: string;
  market: string;
  outcome: string;
  opening_odd: number | null;
  current_odd: number | null;
  price_delta: number | null;
  price_delta_pct: number | null;
  book_consensus_move: number | null;
  book_dispersion: number | null;
  market_direction: number | null;
  n_observations: number;
  n_books: number;
  minutes_since_open: number | null;
  minutes_to_kickoff: number | null;
  status: "MOVING" | "STABLE" | "NO_DATA" | "INSUFFICIENT_DATA";
}

export interface OddsMovementOverview {
  generated_at: string;
  movements: OddsMovement[];
  source: string;
  data_version: string | null;
}

// ------------------------------------------------- CLV e Coverage ----------

export interface ClvEntry {
  match: string;
  market: string;
  outcome: string;
  /** null = sem observação PIT válida no instante da decisão (NO_ENTRY_ODDS) */
  entry_odd: number | null;
  /** Timestamp real da observação que produziu a entrada — nunca o prediction_timestamp */
  entry_timestamp: string | null;
  closing_odd: number | null;
  closing_bookmaker: string | null;
  closing_timestamp: string | null;
  clv_percentage: number | null;
  clv_probability: number | null;
  status: "OK" | "NO_CLOSING_ODDS" | "CLOSING_BEFORE_ENTRY" | "NO_ENTRY_ODDS";
  /** Proveniência da decisão (schema v4) — ausente em registros antigos */
  home?: string | null;
  away?: string | null;
  league?: string | null;
  entry_n_books?: number | null;
  entry_bookmaker?: string | null;
  /** Executabilidade: UNKNOWN enquanto não houver execução real */
  execution_status?: "UNKNOWN" | null;
  /** Ciclo de vida operacional: "ainda não" (PENDING) != "nunca" (NO_CLOSE) */
  lifecycle_state?:
    | "PENDING"
    | "NO_CLOSE"
    | "CLOSED"
    | "INVALID"
    | "MISMATCH"
    | null;
  lifecycle_detail?: string | null;
}

export interface ClvReport {
  generated_at: string;
  total_bets: number;
  bets_with_clv: number;
  /** null = cobertura não medida (nenhuma entrada registrada), nunca 0 fabricado */
  coverage: number | null;
  avg_clv_percentage: number | null;
  median_clv_percentage: number | null;
  positive_clv_rate: number | null;
  avg_clv_probability: number | null;
  by_market: Record<
    string,
    { n: number; with_clv: number; avg_clv_percentage: number | null }
  >;
  entries: ClvEntry[];
  source: string;
  /** Contagem por estado do ciclo de vida (PENDING/NO_CLOSE/CLOSED/...) */
  lifecycle?: Record<string, number>;
}

export interface CoverageReport {
  generated_at: string;
  providers: ProviderHealth[];
  /** null = não medido (sem população observada), nunca 0 fabricado */
  clv_coverage: number | null;
  odds_coverage: number | null;
  xg_coverage: number | null;
  n_fixtures_with_odds: number;
  n_fixtures_total: number;
  /** Casas EFETIVAMENTE observadas nas odds — chave de provider não conta */
  n_bookmakers_active: number;
  bookmakers_observed: string[];
  gaps: { provider: string; gap: string; detail: string }[];
  source: string;
}

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

/** Uma verificação individual da decisão de apostar (auditável). */
export interface DecisionCheck {
  name: string;
  passed: boolean;
  detail: string;
}

/**
 * Decisão do Quant: apostar (BET) ou não apostar (NO_BET).
 *
 * NO_BET é resultado de primeira classe: não vira aposta e não ganha
 * stake — `fraction` é 0 e `reason` preserva o motivo da decisão.
 */
export interface BetDecision {
  action: "BET" | "NO_BET";
  reason: string;
  fraction: number;
  conservative_roi: number | null;
  kelly_full: number | null;
  /**
   * Status da evidência que fundamentou a decisão — vocabulário canônico
   * do domínio (staking). "exploratory" nunca vira "validated" porque
   * existe uma previsão: são estados de evidência, não de output.
   */
  evidence_status: "exploratory" | "validated" | "timestamped" | "real" | "synthetic";
  checks: DecisionCheck[];
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
  /** decisão do Quant sobre a evidência atual (BET | NO_BET), com motivo */
  decision: BetDecision | null;
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
  | "cards"
  | "fixtures"
  | "providers"
  | "coverage"
  | "clv"
  | "movement"
  | "quant";

export type ConfidenceFilter = "all" | "FORTE" | "MEDIA" | "FRACA";

export interface WsMessage<T = unknown> {
  event:
    | "status"
    | "pong"
    | "recalculate:start"
    | "recalculate:progress"
    | "recalculate:done"
    | "recalculate:error"
    | "backtest:start"
    | "backtest:progress";
  payload: T;
}

/** Estado do job assincrono de recalculo (GET /api/recalculate/status). */
export interface RecalculateJobStatus {
  job_id: string | null;
  phase:
    | "idle"
    | "starting"
    | "fixtures"
    | "history"
    | "ratings"
    | "analyzing"
    | "signals"
    | "done"
    | "error"
    | "cancelled";
  progress: number;
  message: string;
  error: string | null;
  snapshot_generated_at: string | null;
  has_snapshot: boolean;
}
