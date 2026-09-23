/**
 * Testes da ClvPage.
 *
 * Contratos de honestidade (I-02/I-03):
 *  - entry_odd pode ser null (NO_ENTRY_ODDS): "—", nunca 0 nem valor inventado;
 *  - coverage pode ser null: "—", nunca 0% fabricado;
 *  - entry_timestamp aparece quando existe;
 *  - NO_ENTRY_ODDS mostra "Sem odd de entrada observada".
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ClvPage from "@/pages/ClvPage";
import { StoreContext, type Store } from "@/store/context";
import { jsonResponse, mockFetch } from "@/test/fixtures";
import type { ClvReport } from "@/types/api";

const noop = () => {};
const makeStore = (overrides: Partial<Store> = {}): Store => ({
  tab: "clv",
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

function makeReport(overrides: Partial<ClvReport> = {}): ClvReport {
  return {
    generated_at: "2026-09-22 12:00:00",
    total_bets: 2,
    bets_with_clv: 1,
    coverage: 0.5,
    avg_clv_percentage: 0.1,
    median_clv_percentage: 0.1,
    positive_clv_rate: 1.0,
    avg_clv_probability: 0.05,
    by_market: {
      "Resultado Final (1X2)": { n: 2, with_clv: 1, avg_clv_percentage: 0.1 },
    },
    entries: [
      {
        match: "Arsenal vs Chelsea",
        market: "Resultado Final (1X2)",
        outcome: "1",
        entry_odd: 2.2,
        entry_timestamp: "2030-01-01T09:55:00Z",
        closing_odd: 2.0,
        closing_bookmaker: "Pinnacle",
        closing_timestamp: "2030-01-01T11:30:00Z",
        clv_percentage: 0.1,
        clv_probability: 0.045,
        status: "OK",
      },
      {
        match: "Arsenal vs Chelsea",
        market: "Resultado Final (1X2)",
        outcome: "X",
        entry_odd: null,
        entry_timestamp: null,
        closing_odd: null,
        closing_bookmaker: null,
        closing_timestamp: null,
        clv_percentage: null,
        clv_probability: null,
        status: "NO_ENTRY_ODDS",
      },
    ],
    source: "football-data.co.uk",
    ...overrides,
  };
}

function renderPage(store: Store) {
  return render(
    <StoreContext.Provider value={store}>
      <ClvPage />
    </StoreContext.Provider>,
  );
}

describe("ClvPage", () => {
  it("renderiza entradas com odd de entrada e entry_timestamp", async () => {
    mockFetch(() => jsonResponse(makeReport()));
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getAllByText("Arsenal vs Chelsea").length).toBeGreaterThan(0),
    );
    expect(screen.getByText("2,20")).toBeInTheDocument();
    expect(screen.getByText("2030-01-01T09:55:00Z")).toBeInTheDocument();
    expect(screen.getAllByText("OK").length).toBeGreaterThan(0);
  });

  it("mostra '—' para entry_odd null — nunca valor inventado", async () => {
    mockFetch(() => jsonResponse(makeReport()));
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getByText("Sem odd de entrada observada")).toBeInTheDocument(),
    );
    // entry_odd null e closing_odd null mostram "—", nao 0
    expect(screen.queryByText("1,00")).not.toBeInTheDocument();
    expect(screen.queryByText("0,0%")).not.toBeInTheDocument();
  });

  it("coverage null aparece como '—' (nao medido), nunca 0% fabricado", async () => {
    mockFetch(() =>
      jsonResponse(
        makeReport({
          coverage: null,
          avg_clv_percentage: null,
          median_clv_percentage: null,
          positive_clv_rate: null,
          avg_clv_probability: null,
        }),
      ),
    );
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getByText(/coverage —/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/coverage 0,0%/)).not.toBeInTheDocument();
  });

  it("mostra estado vazio quando nao ha entradas", async () => {
    mockFetch(() =>
      jsonResponse(makeReport({ total_bets: 0, entries: [] })),
    );
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getByText("Sem entradas de CLV")).toBeInTheDocument(),
    );
  });

  it("mostra erro quando a API falha", async () => {
    mockFetch(() => jsonResponse({ detail: "fora do ar" }, 503));
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeInTheDocument(),
    );
  });
});
