/**
 * Testes da PortfolioPage.
 *
 * Contrato critico: com NO_BET do Quant a pagina precisa EXPLICAR por que
 * a exposicao e zero (decision_action/decision_reason do backend), em vez
 * de mostrar "0 apostas · dentro dos limites" sem contexto.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PortfolioPage from "@/pages/PortfolioPage";
import { StoreContext, type Store } from "@/store/context";
import { jsonResponse, mockFetch } from "@/test/fixtures";
import type { ExposureReport } from "@/types/portfolio";

const noop = () => {};
const makeStore = (overrides: Partial<Store> = {}): Store => ({
  tab: "portfolio",
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

function makeExposure(
  overrides: Partial<ExposureReport> = {},
): ExposureReport {
  return {
    decision_action: "NO_BET",
    decision_reason:
      "Evidência insuficiente: odds sem timestamp de publicação não sustentam aposta real.",
    decision_evidence_status: "exploratory",
    total_exposure: 0,
    total_exposure_pct: 0,
    n_bets: 0,
    within_limits: true,
    violations: [],
    by_match: {},
    ...overrides,
  };
}

function renderPage(store: Store) {
  return render(
    <StoreContext.Provider value={store}>
      <PortfolioPage />
    </StoreContext.Provider>,
  );
}

const PARLAYS_URL = /\/api\/portfolio\/best-parlays/;
const EXPOSURE_URL = /\/api\/portfolio\/exposure/;

function mockApis(exposure: ExposureReport, parlays: unknown[] = []) {
  mockFetch((url: string) => {
    if (EXPOSURE_URL.test(url)) return jsonResponse(exposure);
    if (PARLAYS_URL.test(url)) return jsonResponse(parlays);
    throw new Error(`rota inesperada: ${url}`);
  });
}

describe("PortfolioPage", () => {
  it("explica o NO_BET do Quant: banner com motivo e evidência", async () => {
    mockApis(makeExposure());
    renderPage(makeStore());

    await waitFor(() =>
      expect(
        screen.getByText(/Quant decidiu NÃO APOSTAR/i),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/odds sem timestamp de publicação/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/exploratory/)).toBeInTheDocument();
    // deixa claro que os numeros sao diagnostico, nao aposta autorizada
    expect(screen.getByText(/não são apostas autorizadas/i)).toBeInTheDocument();
  });

  it("sem NO_BET: sem banner de decisão, KPIs normais", async () => {
    mockApis(
      makeExposure({
        decision_action: "BET",
        decision_reason: "evidência validada",
        decision_evidence_status: "timestamped",
        total_exposure: 42.5,
        total_exposure_pct: 0.0425,
        n_bets: 3,
        by_match: { "A vs B": 20, "C vs D": 22.5 },
      }),
    );
    renderPage(makeStore());

    await waitFor(() =>
      expect(screen.getByText("Exposição total")).toBeInTheDocument(),
    );
    expect(
      screen.queryByText(/Quant decidiu NÃO APOSTAR/i),
    ).not.toBeInTheDocument();
  });

  it("mostra erro quando a API de exposição falha", async () => {
    mockFetch((url: string) => {
      if (EXPOSURE_URL.test(url)) return jsonResponse({ detail: "fora do ar" }, 503);
      if (PARLAYS_URL.test(url)) return jsonResponse([]);
      throw new Error(`rota inesperada: ${url}`);
    });
    renderPage(makeStore());

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument(),
    );
  });

  it("mostra estado vazio quando não há múltiplas com o filtro", async () => {
    mockApis(makeExposure(), []);
    renderPage(makeStore());

    await waitFor(() =>
      expect(
        screen.getByText(/nenhuma múltipla com EV acima do filtro/i),
      ).toBeInTheDocument(),
    );
  });
});
