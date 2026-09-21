/**
 * Contratos do modulo de PORTFOLIO — espelho de
 * `GET /api/portfolio/best-parlays` e `GET /api/portfolio/exposure`
 * em `betgsn/api/server.py`.
 *
 * Convencoes identicas ao resto da API: fracoes em [0,1]
 * (joint_probability, ev, kelly, risk_score, total_exposure_pct) e
 * dinheiro na moeda da banca (stake, payout, total_exposure).
 * O frontend so formata.
 */

/** Uma perna de uma multipla. Subconjunto de `portfolio.parlay.ParlayLeg`
 *  serializado pelo endpoint (sem ev/edge/correlation_group). */
export interface ParlayLeg {
  match: string;
  market: string;
  outcome: string;
  odd: number;
  bookmaker: string;
  model_prob: number;
}

/** Uma multipla candidata ja calculada pelo backend.
 *
 *  ATENCAO: `joint_probability` e o produto das `model_prob` das pernas
 *  (independencia assumida). O endpoint nao aplica ajuste de correlacao,
 *  portanto multiplas com 2+ pernas do MESMO jogo tem EV superestimado. */
export interface ParlayCandidate {
  legs: ParlayLeg[];
  n_legs: number;
  combined_odd: number;
  joint_probability: number;
  ev: number;
  kelly: number;
  stake: number;
  payout: number;
  /** "2-leg", "3-leg", ... */
  category: string;
  /** 0-1, maior = mais arriscado */
  risk_score: number;
}

/** Exposicao agregada das apostas simples do snapshot atual. */
export interface ExposureReport {
  total_exposure: number;
  /** fracao da banca, ja em [0,1] */
  total_exposure_pct: number;
  n_bets: number;
  within_limits: boolean;
  violations: string[];
  /** stake somado por jogo */
  by_match: Record<string, number>;
}
