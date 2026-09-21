/**
 * Testes da CardsPage.
 *
 * Mesmos contratos da CornersPage: dado real, ausencia de preco explicitada,
 * estados de loading/vazio/erro, e nada de zero inventado.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import CardsPage from "@/pages/CardsPage";
import { StoreContext, type Store } from "@/store/context";
import { jsonResponse, mockFetch } from "@/test/fixtures";
import type { GameAnalysis } from "@/types/api";

const noop = () => {};
const makeStore = (overrides: Partial<Store> = {}): Store => ({
  tab: "cards",
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
  dataVersion: 0,
  error: null,
  toasts: [],
  socketState: "closed",
  setTab: noop,
  setConfig: noop,
  hydrate: noop,
  recalculate: async () => {},
  pushToast: noop,
  dismissToast: noop,
  ...overrides,
});

function makeGame(overrides: Partial<GameAnalysis> = {}): GameAnalysis {
  const markets = overrides.markets ?? [
    {
      market: "Cartoes",
      outcomes: {
        "Cartoes Over 2.5": 0.72,
        "Cartoes Under 2.5": 0.28,
        "Cartoes Over 3.5": 0.48,
        "Cartoes Under 3.5": 0.52,
        "Cartoes Over 4.5": 0.26,
        "Cartoes Under 4.5": 0.74,
      },
    },
  ];
  const team = (name: string) => ({
    name,
    attack: 1.1,
    defense: 0.9,
    strength: 1.22,
    goals_for: 1.5,
    goals_against: 1.0,
    xg_for: null,
    xg_against: null,
    corners_for: 5.8,
    corners_against: 4.6,
    cards_for: 2.2,
    cards_against: 2.4,
    shots_for: 13.5,
    shots_on_target_for: 4.7,
    form_points: 1.5,
    matches_played: 20,
  });
  return {
    id: "E0|Arsenal vs Chelsea",
    match: "Arsenal vs Chelsea",
    home: "Arsenal",
    away: "Chelsea",
    league: "Premier League (England)",
    kickoff: "2026-09-20 16:00",
    round_label: "Rodada 5",
    lambda_home: 1.6,
    lambda_away: 1.1,
    prob_home: 0.45,
    prob_draw: 0.27,
    prob_away: 0.28,
    prob_over_25: 0.52,
    prob_btts: 0.51,
    prob_home_corners_over_55: 0.35,
    prob_cards_over_35: 0.48,
    top_scorelines: [{ home_goals: 1, away_goals: 0, prob: 0.12 }],
    markets,
    ratings_home: team("Arsenal"),
    ratings_away: team("Chelsea"),
    n_markets_with_odds: 2,
    signal_count: 0,
    ...overrides,
  };
}

function renderPage(store: Store) {
  return render(
    <StoreContext.Provider value={store}>
      <CardsPage />
    </StoreContext.Provider>,
  );
}

describe("CardsPage", () => {
  it("renderiza as probabilidades do modelo a partir de /api/games", async () => {
    mockFetch(() => jsonResponse([makeGame()]));
    renderPage(makeStore());
    await waitFor(() => expect(screen.getByText("Arsenal vs Chelsea")).toBeInTheDocument());
    // total >3.5 = 0.48 -> "48,0%"
    expect(screen.getByText("48,0%")).toBeInTheDocument();
    // medias de cartoes por time
    expect(screen.getByText("2,2")).toBeInTheDocument();
  });

  it("declara perfil de arbitro como nao coletado", async () => {
    mockFetch(() => jsonResponse([makeGame()]));
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getByText(/não coletado/)).toBeInTheDocument(),
    );
  });

  it("declara ausencia de preco de mercado", async () => {
    mockFetch(() => jsonResponse([makeGame()]));
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getByText(/Sem preço de mercado/)).toBeInTheDocument(),
    );
  });

  it("mostra n/d quando o mercado de cartoes esta ausente", async () => {
    mockFetch(() => jsonResponse([makeGame({ markets: [] })]));
    renderPage(makeStore());
    await waitFor(() => expect(screen.getByText("Arsenal vs Chelsea")).toBeInTheDocument());
    expect(screen.getAllByText("n/d").length).toBeGreaterThan(0);
    expect(screen.queryByText("0,0%")).not.toBeInTheDocument();
  });

  it("mostra estado vazio quando nao ha jogos", async () => {
    mockFetch(() => jsonResponse([]));
    renderPage(makeStore());
    await waitFor(() =>
      expect(screen.getByText("Nenhum jogo encontrado")).toBeInTheDocument(),
    );
  });

  it("mostra erro quando a API falha", async () => {
    mockFetch(() => jsonResponse({ detail: "fora do ar" }, 503));
    renderPage(makeStore());
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
