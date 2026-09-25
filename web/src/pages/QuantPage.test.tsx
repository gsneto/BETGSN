/**
 * Testes da QuantPage.
 *
 * Contratos de honestidade:
 *  - cache/artefato ausente mostra o estado explícito (NO_VALID_CACHE /
 *    MISSING) com instrução da tool offline — nunca número inventado;
 *  - model vs market exibe as fontes SEPARADAS (market_raw/model_raw);
 *  - line-shopping expõe o delta de preço da MESMA população;
 *  - CLV lifecycle distingue PENDING de NO_CLOSE;
 *  - nada aqui exibe decisão (action/stake) — observabilidade não decide.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import QuantPage from "@/pages/QuantPage";
import { StoreContext, type Store } from "@/store/context";
import { jsonResponse, mockFetch } from "@/test/fixtures";

const noop = () => {};
const makeStore = (overrides: Partial<Store> = {}): Store => ({
  tab: "quant",
  config: {
    bankroll: 1000,
    kelly_fraction: 0.25,
    min_ev: 0.02,
    stake_cap: 0.01,
    max_exposure: 0.25,
    use_xg: true,
    rounds: 3,
  },
  summary: null,
  status: null,
  recalculating: false,
  recalcJob: null,
  dataVersion: 0,
  error: null,
  toasts: [],
  socketState: "closed",
  setTab: noop,
  setConfig: noop,
  hydrate: noop,
  recalculate: async () => {},
  cancelRecalculate: noop,
  pushToast: noop,
  dismissToast: noop,
  ...overrides,
});

/** Roteia cada URL /api/quant/* ao payload correspondente. */
function routeQuant(responses: Record<string, unknown>) {
  mockFetch((input: RequestInfo | URL) => {
    const url = String(input);
    for (const [suffix, body] of Object.entries(responses)) {
      if (url.endsWith(suffix)) return jsonResponse(body);
    }
    return jsonResponse({ detail: "rota não mapeada no teste" }, 404);
  });
}

function renderPage(store: Store = makeStore()) {
  return render(
    <StoreContext.Provider value={store}>
      <QuantPage />
    </StoreContext.Provider>,
  );
}

const benchmarks = {
  kind: "benchmark_manifest",
  generated_at: "2026-09-23 20:39:16",
  code_version: "1.1.0",
  benchmark_reference: "ETAPA_19",
  corpus: {
    signature: "583|1790124446",
    source: "football-data.co.uk",
    main_files: 565,
  },
  protocol: {
    train_days: 730,
    test_days: 365,
    gap_days_embargo: 2,
    bootstrap_seed: 424242,
    candidate_max_odds: [1.2, 1.25, 1.3, 1.4],
    rule: { max_odd: 1.3, min_books: 3 },
  },
  caches: {
    "value_validation_oos.json": { status: "VALID", expected_fingerprint: "a" },
    "model_validation_oos.json": { status: "STALE", expected_fingerprint: "b" },
    "value_validation.json": { status: "MISSING" },
  },
  reproducibility: { all_valid: false, note: "declaração" },
};

const modelMarket = {
  status: "OK",
  model: "BASELINE_V1",
  n_bets_oos: 490736,
  market_raw: { brier: 0.2005, logloss: 0.5869, ece: 0.0114, n: 490736 },
  market_fair: { brier: 0.2005, logloss: 0.587, ece: 0.0064, n: 490736 },
  model_raw: { brier: 0.2091, logloss: 0.607, ece: 0.0027, n: 490736 },
  model_calibrated: { brier: 0.2095, logloss: 0.6083, ece: 0.0147, n: 490736 },
  paired_model_vs_raw: { verdict: "model_worse" },
};

const lineShopping = {
  status: "OK",
  n_lines_rule: 6751,
  rule_population_by_price: {
    best: { n: 6751, roi: 0.0161 },
    second: { n: 6751, roi: 0.0071 },
    median: { n: 6751, roi: -0.0036 },
    worst: { n: 6751, roi: -0.0252 },
  },
  limitations: ["odds do corpus sem timestamp de publicação"],
  strategy_model_decomposition: {
    n_rows: 181172,
    roi_best: -0.0489,
    roi_median: -0.1113,
    delta_price_effect: 0.0624,
  },
};

const ml = {
  status: "OK",
  protocol: { gap_days_embargo: 2 },
  models: {
    elo: {
      n_bets_oos: 300000,
      model_raw: { brier: 0.21, logloss: 0.61, ece: 0.02, n: 300000 },
      market_raw: { brier: 0.2, logloss: 0.59, ece: 0.01, n: 300000 },
      delta_logloss_vs_market_raw: 0.02,
    },
  },
  ensemble: { status: "PENDENTE", reason: "stacking OOS por janela" },
  declarations: ["sem ranking de modelos"],
};

const clvStatus = {
  status: "OK",
  lifecycle: { PENDING: 480, NO_CLOSE: 0, CLOSED: 0, INVALID: 0, MISMATCH: 0 },
  n_entries: 480,
  clv_prospective: { mean: 0, n: 0, prospective: true },
  promotion_gate_note: "n=0: Promotion Gate permanece BLOCKED",
  oos_windows_valid: 24,
  lifecycle_note: "PENDING = kickoff no futuro",
  evaluated_at: "2026-09-23T21:00:00Z",
};

const clvReport = {
  generated_at: "2026-09-23 21:00:00",
  total_bets: 0,
  bets_with_clv: 0,
  coverage: null,
  entries: [],
  source: "football-data.co.uk",
};

const clvProgress = {
  target: 200,
  closed: 0,
  pending: 891,
  no_close: 3,
  invalid: 0,
  mismatch: 0,
  n_entries: 894,
  remaining: 200,
  status: "CLV_INSUFFICIENT_DATA",
  note: "CLV válido = CLOSED + closing real.",
};

const executionStatus = {
  n_executions: 0,
  status: "UNKNOWN",
  note: "Sem execução registrada, o estado é UNKNOWN.",
};

const alphaLab = {
  status: "OK",
  evaluations: {
    n_observations: 51865,
    dataset_fingerprint: "1b6e922b85ad2c08",
    evaluations: {
      BOOKMAKER_OUTLIER: {
        alpha_id: "bookmaker_outlier",
        signal_type: "BOOKMAKER_OUTLIER",
        n: 40919,
        metric_kind: "directional",
        primary_metric: 0.0005,
        primary_label: "signed_market_move_5m",
        ci_low: -0.0001,
        ci_high: 0.0012,
        status: "NO_EVIDENCE",
        limitations: [],
      },
    },
  },
  clv: { progress: { closed: 0, target: 200 } },
  execution: { execution: { status: "UNKNOWN", n_measured: 0 } },
  capture_readiness: {
    readiness: { status: "WAITING_FOR_PROVIDER_QUOTA", blocked: ["The Odds API"] },
  },
  promotion: { verdict: "NO_BET", production_eligible: false },
};

function fullRoutes() {
  return routeQuant({
    "/api/quant/benchmarks": benchmarks,
    "/api/quant/model-vs-market": modelMarket,
    "/api/quant/line-shopping": lineShopping,
    "/api/quant/ml": ml,
    "/api/quant/clv/status": clvStatus,
    "/api/quant/clv/progress": clvProgress,
    "/api/quant/execution/status": executionStatus,
    "/api/quant/alpha-lab": alphaLab,
    "/api/clv": clvReport,
  });
}

describe("QuantPage", () => {
  it("exibe o painel do Alpha Lab com progresso CLV e veredito NO_BET", async () => {
    fullRoutes();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("Alpha Lab — evidência")).toBeInTheDocument(),
    );
    // progresso CLV 0/200 e status por sinal
    await waitFor(() => expect(screen.getByText(/0 \/ 200/)).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByText("NO_EVIDENCE")).toBeInTheDocument(),
    );
    // veredito de promoção explícito
    expect(screen.getAllByText("NO_BET").length).toBeGreaterThan(0);
  });

  it("exibe a referência com o estado de cada cache (VALID/STALE/MISSING)", async () => {
    fullRoutes();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("Referência de benchmark (Etapa 19)")).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText("reprodutibilidade rupturada")).toBeInTheDocument());
    expect(screen.getByText("value_validation_oos.json")).toBeInTheDocument();
    expect(screen.getByText("model_validation_oos.json")).toBeInTheDocument();
    // com all_valid=false o badge positivo NÃO aparece
    expect(screen.queryByText("reproduzível")).toBeNull();
  });

  it("exibe model vs market com fontes separadas e veredito pareado", async () => {
    fullRoutes();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("Modelo vs Mercado — 24 janelas OOS")).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText("model_worse")).toBeInTheDocument());
    // market e model são linhas distintas — nada de rótulo trocado
    expect(screen.getAllByText(/MARKET RAW/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/MODEL RAW/).length).toBeGreaterThan(0);
  });

  it("exibe a decomposição do line-shopping na MESMA população", async () => {
    fullRoutes();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("Auditoria do line-shopping")).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText(/delta puro de preço/)).toBeInTheDocument(),
    );
    // fmtPct usa 1 dígito: 6,24% -> "6,2%"
    expect(screen.getByText("6,2%")).toBeInTheDocument();
  });

  it("exibe o ciclo de vida do CLV: PENDING != NO_CLOSE", async () => {
    fullRoutes();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("CLV prospectivo — ciclo de vida")).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText("480")).toBeInTheDocument());
    expect(screen.getAllByText("PENDING").length).toBeGreaterThan(0);
    // nota específica do promotion gate (evita casar com a legenda do Alpha Lab)
    expect(
      screen.getByText(/Promotion Gate permanece BLOCKED/),
    ).toBeInTheDocument();
  });

  it("declara Ensemble como PENDENTE e não fabrica ranking", async () => {
    fullRoutes();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("Modelos experimentais — mesmas 24 janelas")).toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText(/Ensemble: PENDENTE/)).toBeInTheDocument());
    expect(screen.queryByText(/vencedor/i)).toBeNull();
  });

  it("exibe o Ensemble OK com métricas e protocolo de stacking OOS", async () => {
    routeQuant({
      "/api/quant/benchmarks": benchmarks,
      "/api/quant/model-vs-market": modelMarket,
      "/api/quant/line-shopping": lineShopping,
      "/api/quant/ml": {
        ...ml,
        ensemble: {
          status: "OK",
          model: "ENSEMBLE_STACK_V1",
          n_bets_oos: 490736,
          model_raw: { brier: 0.2, logloss: 0.59, ece: 0.01, n: 490736 },
          market_raw: { brier: 0.2005, logloss: 0.5869, ece: 0.0114, n: 490736 },
          delta_logloss_vs_market_raw: 0.0031,
          stacking_protocol: {
            base_models: ["elo", "xgboost", "lightgbm"],
            n_folds: 3,
            meta_model: "LogisticRegression",
            note: "stacking OOS por janela",
          },
        },
      },
      "/api/quant/clv/status": clvStatus,
      "/api/clv": clvReport,
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/stacking OOS por janela/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/elo \+ xgboost \+ lightgbm/)).toBeInTheDocument();
    expect(screen.getByText(/3 folds rolling-origin no TRAIN/)).toBeInTheDocument();
  });

  it("CLV: estatísticas nulas-seguras — n=0 mostra em falta, nunca 0%", async () => {
    routeQuant({
      "/api/quant/benchmarks": benchmarks,
      "/api/quant/model-vs-market": modelMarket,
      "/api/quant/line-shopping": lineShopping,
      "/api/quant/ml": ml,
      "/api/quant/clv/status": {
        ...clvStatus,
        clv_statistics: {
          n: 0, mean: null, median: null, p10: null, p25: null,
          p75: null, p90: null, positive_rate: null,
          last_closing_timestamp: null,
        },
        close_rate: null,
        resolve_rate: null,
        capture: {
          last_observation_timestamp: null,
          n_observations: 120,
          n_matches: 40,
          providers: { "The Odds API": 120 },
        },
        provider_issues: ["ParlayAPI"],
        provider_health: {
          ParlayAPI: {
            state: "DEGRADED", consecutive_failures: 2,
            last_success_at: "", last_error: "timeout",
          },
        },
      },
      "/api/clv": clvReport,
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/CLV prospectivo — ciclo de vida/)).toBeInTheDocument(),
    );
    // n=0: média em falta — nunca "0,0%" (ausência não é CLV zero)
    await waitFor(() => expect(screen.getAllByText("—").length).toBeGreaterThan(0));
    expect(screen.getByText(/sem amostra/)).toBeInTheDocument();
    expect(screen.getAllByText(/não medido/).length).toBeGreaterThan(0);
    // provider com problema aparece nominalmente (texto único no badge)
    expect(screen.getByText("ParlayAPI: DEGRADED")).toBeInTheDocument();
  });

  it("mostra estado explícito quando o cache de modelo é stale/ausente", async () => {
    routeQuant({
      "/api/quant/benchmarks": benchmarks,
      "/api/quant/model-vs-market": {
        status: "NO_VALID_CACHE",
        detail: "cache ausente — rode tools/model_validation.py",
      },
      "/api/quant/line-shopping": { status: "MISSING", detail: "rode a tool" },
      "/api/quant/ml": { status: "MISSING", detail: "rode a tool" },
      "/api/quant/clv/status": clvStatus,
      "/api/clv": clvReport,
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/tools\/model_validation\.py/)).toBeInTheDocument(),
    );
    // nunca mostra números do modelo quando o cache não é válido
    expect(screen.queryByText("0,5869")).toBeNull();
  });

  it("nunca exibe stake/ação operacional: observabilidade não decide", async () => {
    fullRoutes();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText("Auditoria do line-shopping")).toBeInTheDocument(),
    );
    // O painel do Alpha Lab expõe o VEREDITO de promoção (evidência real),
    // nunca uma ação/stake operacional.
    expect(screen.queryByText(/stake/i)).toBeNull();
    expect(screen.queryByText(/APOSTAR/)).toBeNull();
  });
});
