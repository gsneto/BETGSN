/**
 * Tipos dos endpoints /api/quant — observabilidade quantitativa.
 *
 * Tudo é leitura: nenhum endpoint recalcula, decide ou promove. Cache
 * ausente/stale chega como status explícito, nunca como números de
 * outra medição.
 */

/** Métricas de UMA fonte de probabilidade numa avaliação. */
export interface QuantSourceMetrics {
  brier: number | null;
  logloss: number | null;
  ece: number | null;
  n: number;
}

export interface QuantPairedComparison {
  verdict?: string;
  delta_mean?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  [k: string]: unknown;
}

/** GET /api/quant/model-vs-market */
export interface QuantModelVsMarket {
  status: "OK" | "NO_VALID_CACHE";
  detail?: string;
  model?: string;
  generated_at?: string;
  cache_fingerprint?: string;
  n_bets_oos?: number;
  n_windows_valid?: number;
  n_windows?: number;
  market_raw?: QuantSourceMetrics;
  market_fair?: QuantSourceMetrics;
  model_raw?: QuantSourceMetrics;
  model_calibrated?: QuantSourceMetrics;
  paired_model_vs_raw?: QuantPairedComparison | null;
  paired_model_vs_fair?: QuantPairedComparison | null;
  note?: string;
}

/** GET /api/quant/benchmarks (manifest da referência) */
export interface QuantBenchmarkManifest {
  kind: string;
  generated_at: string;
  code_version: string;
  benchmark_reference: string;
  corpus: {
    signature: string;
    source: string;
    main_files?: number;
    extra_files?: number;
    total_mb?: number;
  };
  protocol: {
    train_days: number;
    test_days: number;
    gap_days_embargo: number;
    bootstrap_seed: number;
    candidate_max_odds: number[];
    rule: { max_odd: number; min_books: number };
  };
  caches: Record<
    string,
    { status: "VALID" | "STALE" | "MISSING"; expected_fingerprint?: string }
  >;
  reproducibility: { all_valid: boolean; note: string };
}

/** GET /api/quant/line-shopping (auditoria) */
export interface QuantLineShopping {
  status: "OK" | "MISSING";
  detail?: string;
  n_lines_total?: number;
  n_lines_rule?: number;
  rule_population_by_price?: Record<
    string,
    { n: number; roi: number | null }
  >;
  price_uplift?: {
    best_vs_median?: { mean: number | null; median: number | null };
    best_vs_second?: { mean: number | null };
  };
  limitations?: string[];
  strategy_model_decomposition?: {
    n_rows?: number;
    roi_best?: number | null;
    roi_median?: number | null;
    delta_price_effect?: number | null;
  } | null;
}

/** GET /api/quant/ml (modelos experimentais, 24 janelas) */
export interface QuantMl {
  status: "OK" | "MISSING";
  detail?: string;
  protocol?: { gap_days_embargo?: number };
  models?: Record<
    string,
    {
      n_bets_oos?: number;
      model_raw?: QuantSourceMetrics;
      market_raw?: QuantSourceMetrics;
      delta_logloss_vs_market_raw?: number | null;
    }
  >;
  /** Ensemble: PENDENTE (motivo) ou OK com métricas do stacking OOS. */
  ensemble?: {
    status: string;
    reason?: string;
    model?: string;
    n_bets_oos?: number;
    model_raw?: QuantSourceMetrics;
    model_calibrated?: QuantSourceMetrics;
    market_raw?: QuantSourceMetrics;
    market_fair?: QuantSourceMetrics;
    delta_logloss_vs_market_raw?: number | null;
    delta_logloss_vs_market_fair?: number | null;
    paired_model_vs_raw?: QuantPairedComparison | null;
    paired_model_vs_fair?: QuantPairedComparison | null;
    stacking_protocol?: {
      base_models: string[];
      n_folds: number;
      meta_model: string;
      note: string;
    };
  };
  declarations?: string[];
}

/** Estatísticas nulas-seguras do CLV válido (n=0 => mean/median null). */
export interface QuantClvStatistics {
  n: number;
  mean: number | null;
  median: number | null;
  p10: number | null;
  p25: number | null;
  p75: number | null;
  p90: number | null;
  positive_rate: number | null;
  last_closing_timestamp: string | null;
}

/** GET /api/quant/clv/status */
export interface QuantClvStatus {
  status: "OK";
  lifecycle: Record<string, number>;
  n_entries: number;
  clv_prospective: { mean: number; n: number; prospective: boolean };
  clv_statistics?: QuantClvStatistics;
  /** CLOSED/(CLOSED+NO_CLOSE) — null quando nada foi medido. */
  close_rate?: number | null;
  /** 1 - MISMATCH/n — entradas que resolvem a observações do store. */
  resolve_rate?: number | null;
  capture?: {
    last_observation_timestamp: string | null;
    n_observations: number;
    n_matches: number;
    providers: Record<string, number>;
  };
  provider_health?: Record<
    string,
    {
      state: string;
      consecutive_failures: number;
      last_success_at: string;
      last_error: string;
    }
  >;
  provider_issues?: string[];
  promotion_gate_note: string;
  oos_windows_valid: number;
  lifecycle_note: string;
  evaluated_at: string;
}
