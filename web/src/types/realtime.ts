/**
 * Contratos do terminal de mercado em tempo real (mirror do backend
 * betgsn/api/realtime.py + betgsn/realtime/*).
 *
 * Fontes separadas, nunca misturadas: market_raw (mediana entre casas),
 * market_fair (devig) e model (pipeline) chegam em campos distintos.
 * Sinal informativo NAO e aposta: `production` sempre NO_BET.
 */

export type FreshnessState = "FRESH" | "RECENT" | "STALE" | "UNKNOWN";

export type MatchStatus = "PRE_MATCH" | "IN_PLAY" | "FINISHED" | "UNKNOWN";

export type SignalStatus = "ACTIVE" | "STALE" | "EXPIRED";

export interface RealtimeBoot {
  building: boolean;
  error: string;
  engine_ready: boolean;
  engine_running: boolean;
}

export interface BookPrice {
  bookmaker: string;
  price: number;
  timestamp: string;
  provider: string;
  age_seconds: number | null;
  freshness: FreshnessState;
}

export interface SelectionView {
  selection: string;
  books: BookPrice[];
  best: BookPrice;
  second_best: BookPrice | null;
  worst: BookPrice;
  median: number;
  mean: number;
  n_books: number;
  dispersion: number | null;
  best_vs_median: number | null;
  best_vs_second: number | null;
  last_update: string;
}

export interface MarketView {
  event_key: string;
  market: string;
  selections: SelectionView[];
  fair_probabilities: Record<string, number>;
  overround: number | null;
  devig_method: string;
  n_books: number;
  last_update: string;
  timestamp_span_seconds: number;
  complete: boolean;
}

export interface EventView {
  event_key: string;
  home: string;
  away: string;
  kickoff: string;
  league: string;
  matched: boolean;
  match_status: MatchStatus;
  markets: MarketView[];
  last_update: string;
}

export interface RealtimeSignal {
  signal_id: string;
  alpha_id: string;
  signal_type: string;
  event_key: string;
  market: string;
  selection: string;
  timestamp: string;
  observed_at: string;
  reason: string;
  evidence: Record<string, unknown>;
  market_price: number | null;
  fair_price: number | null;
  model_price: number | null;
  best_price: number | null;
  median: number | null;
  freshness: string;
  bookmakers: string[];
  status: SignalStatus;
  production: string;
  evidence_status: string;
}

export interface ProviderLoopStatus {
  provider: string;
  interval_seconds: number;
  ticks: number;
  failures: number;
  last_tick_at: string;
  last_success_at: string;
  last_failure_at: string;
  last_error: string;
}

export interface RealtimeEngineStatus {
  running: boolean;
  started_at: string;
  stopped_at: string;
  last_error: string;
  last_heartbeat: string;
  sport_keys: string[];
  interval_seconds: number;
  providers: Record<string, ProviderLoopStatus>;
  provider_health_store: Record<string, Record<string, unknown>>;
  state: {
    events: number;
    events_matched: number;
    events_unmatched: number;
    lines: number;
    problems: number;
  };
  signals: { active: number; created_total: number; expired_total: number };
  bus: {
    published: number;
    deduped: number;
    dropped: number;
    subscribers: number;
    last_event: { event_type: string; event_timestamp: string } | null;
  };
  last_quote_at: string;
  last_movement_at: string;
  last_signal_at: string;
  now: string;
}

export interface LastMoveInfo {
  event_key: string;
  market: string;
  books_moved: string[];
  last_move_at: string;
  moves: {
    selection: string;
    bookmaker: string;
    old_price: number;
    new_price: number;
    direction: string;
    new_timestamp: string;
  }[];
}

export interface RealtimeBoard {
  generated_at: string;
  events: EventView[];
  signals: RealtimeSignal[];
  last_moves: Record<string, LastMoveInfo>;
  problems: QualityProblem[];
  boot: RealtimeBoot;
}

export interface RealtimeStatusResponse {
  engine: RealtimeEngineStatus | null;
  boot: RealtimeBoot;
}

export interface RealtimeProvidersResponse {
  providers: Record<string, ProviderLoopStatus>;
  provider_health_store: Record<string, Record<string, unknown>>;
  state: RealtimeEngineStatus["state"];
  boot: RealtimeBoot;
}

export interface MovementTimelineRow {
  market: string;
  selection: string;
  bookmaker: string;
  price: number;
  timestamp: string;
  provider: string;
  minutes_before_kickoff: number;
}

export interface ModelComparison {
  status: string;
  source: string;
  generated_at?: string;
  model_status?: string;
  model: {
    lambda_home: number;
    lambda_away: number;
    markets: Record<string, Record<string, number>>;
  } | null;
}

export interface ClvEventSummary {
  n: number;
  status: string;
  entries: {
    market: string;
    outcome: string;
    entry_odd: number;
    entry_timestamp: string;
    kickoff: string;
    source: string;
    execution_status: string;
  }[];
}

export interface QualityProblem {
  reason: string;
  provider: string;
  event_id: string;
  bookmaker: string;
  market: string;
  selection: string;
  timestamp: string;
  price: number | null;
}

export interface RealtimeMatchDetail {
  event: EventView;
  signals: RealtimeSignal[];
  movement_timeline: MovementTimelineRow[];
  model_comparison: ModelComparison;
  clv: ClvEventSummary;
  problems: QualityProblem[];
}

export interface AlphaSpecView {
  alpha_id: string;
  name: string;
  version: string;
  status: string;
  hypothesis: string;
  required_data: string[];
  signal_types: string[];
  explanation: string;
}

export interface RealtimeSignalsResponse {
  generated_at: string;
  signals: RealtimeSignal[];
  alphas: Record<string, AlphaSpecView>;
}

/** Evento SSE: envelope unico com event_id/event_timestamp. */
export interface RealtimeStreamEvent {
  event_id: string;
  event_type: string;
  event_timestamp: string;
  payload: Record<string, unknown>;
}
