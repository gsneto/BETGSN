/**
 * Testes do terminal LIVE: página e hook de stream.
 *
 * Rede mockada com `mockFetch` (convencao do projeto) e EventSource
 * fake injetado: verificamos estados de conexao, dedup de eventos e
 * que o board renderiza colunas/estados honestos (— quando nao ha
 * dado, NO BET quando ha sinal, n = 0 no CLV).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import LivePage from "@/pages/LivePage";
import { useRealtimeStream } from "@/hooks/useRealtimeStream";
import { jsonResponse, mockFetch } from "@/test/fixtures";
import type {
  RealtimeBoard,
  RealtimeProvidersResponse,
  RealtimeStatusResponse,
} from "@/types/realtime";

const KICKOFF = "2026-09-28T19:00:00Z";
const EVENT_KEY = "lens|lyon|2026-09-28T19:00:00Z";
const MARKET = "Resultado Final (1X2)";

function boardFixture(): RealtimeBoard {
  const book = (bookmaker: string, price: number) => ({
    bookmaker,
    price,
    timestamp: "2026-09-24T11:59:00Z",
    provider: "ParlayAPI",
    age_seconds: 60,
    freshness: "FRESH" as const,
  });
  return {
    generated_at: "2026-09-24T12:00:00Z",
    boot: { building: false, error: "", engine_ready: true, engine_running: true },
    problems: [],
    last_moves: {
      [`${EVENT_KEY}|${MARKET}`]: {
        event_key: EVENT_KEY,
        market: MARKET,
        books_moved: ["Book A", "Book B"],
        last_move_at: "2026-09-24T11:45:00Z",
        moves: [],
      },
    },
    events: [
      {
        event_key: EVENT_KEY,
        home: "Lens",
        away: "Lyon",
        kickoff: KICKOFF,
        league: "Ligue 1",
        matched: true,
        match_status: "PRE_MATCH",
        last_update: "2026-09-24T11:59:00Z",
        markets: [
          {
            event_key: EVENT_KEY,
            market: MARKET,
            devig_method: "multiplicative",
            n_books: 3,
            last_update: "2026-09-24T11:59:00Z",
            timestamp_span_seconds: 0,
            complete: true,
            overround: 1.04,
            fair_probabilities: { "1": 0.45, X: 0.27, "2": 0.28 },
            selections: [
              {
                selection: "1",
                median: 2.2,
                mean: 2.2,
                n_books: 3,
                dispersion: 0.05,
                best_vs_median: 0.06,
                best_vs_second: 0.02,
                last_update: "2026-09-24T11:59:00Z",
                best: book("Book C", 2.26),
                second_best: book("Book A", 2.24),
                worst: book("Book B", 2.1),
                books: [book("Book A", 2.24), book("Book B", 2.1), book("Book C", 2.26)],
              },
            ],
          },
        ],
      },
    ],
    signals: [
      {
        signal_id: "abc123",
        alpha_id: "line_shopping",
        signal_type: "BEST_PRICE_GAP",
        event_key: EVENT_KEY,
        market: MARKET,
        selection: "1",
        timestamp: "2026-09-24T12:00:00Z",
        observed_at: "2026-09-24T11:59:00Z",
        reason: "Melhor preco de 1: 2.26 em Book C, acima da mediana (2.2)",
        evidence: {},
        market_price: 2.2,
        fair_price: 2.22,
        model_price: null,
        best_price: 2.26,
        median: 2.2,
        freshness: "FRESH",
        bookmakers: ["Book A", "Book B", "Book C"],
        status: "ACTIVE",
        production: "NO_BET",
        evidence_status: "OBSERVED",
      },
    ],
  };
}

function statusFixture(): RealtimeStatusResponse {
  return {
    boot: { building: false, error: "", engine_ready: true, engine_running: true },
    engine: {
      running: true,
      started_at: "2026-09-24T10:00:00Z",
      stopped_at: "",
      last_error: "",
      last_heartbeat: "2026-09-24T12:00:00Z",
      sport_keys: ["soccer_france_ligue_one"],
      interval_seconds: 300,
      providers: {
        ParlayAPI: {
          provider: "ParlayAPI",
          interval_seconds: 300,
          ticks: 3,
          failures: 0,
          last_tick_at: "2026-09-24T12:00:00Z",
          last_success_at: "2026-09-24T12:00:00Z",
          last_failure_at: "",
          last_error: "",
        },
      },
      provider_health_store: {},
      state: { events: 1, events_matched: 1, events_unmatched: 0, lines: 3, problems: 0 },
      signals: { active: 1, created_total: 1, expired_total: 0 },
      bus: { published: 10, deduped: 2, dropped: 0, subscribers: 1, last_event: null },
      last_quote_at: "2026-09-24T11:59:00Z",
      last_movement_at: "2026-09-24T11:45:00Z",
      last_signal_at: "2026-09-24T12:00:00Z",
      now: "2026-09-24T12:00:00Z",
    },
  };
}

function providersFixture(): RealtimeProvidersResponse {
  return {
    boot: { building: false, error: "", engine_ready: true, engine_running: true },
    providers: statusFixture().engine!.providers,
    provider_health_store: {},
    state: statusFixture().engine!.state,
  };
}

function stubRealtimeApi() {
  return mockFetch((url) => {
    if (url.includes("/api/realtime/board")) return jsonResponse(boardFixture());
    if (url.includes("/api/realtime/status")) return jsonResponse(statusFixture());
    if (url.includes("/api/realtime/providers")) return jsonResponse(providersFixture());
    if (url.includes("/api/realtime/match")) {
      return jsonResponse({
        event: boardFixture().events[0],
        signals: [],
        movement_timeline: [],
        model_comparison: { status: "NO_MODEL", source: "pipeline", model: null },
        clv: { n: 0, status: "NO_ENTRIES", entries: [] },
        problems: [],
      });
    }
    return jsonResponse({ error: `rota nao mockada: ${url}` }, 404);
  });
}

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners: Record<string, EventListener> = {};
  readyState = 0;
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: EventListener) {
    this.listeners[type] = listener;
  }
  close() {
    this.readyState = 2;
  }
  emit(type: string, data: unknown) {
    this.listeners[type]?.(
      new MessageEvent(type, { data: JSON.stringify(data) }),
    );
  }
}

describe("LivePage", () => {
  beforeEach(() => {
    stubRealtimeApi();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("mostra engine rodando, board com melhor preco e sinal com motivo", async () => {
    render(<LivePage />);

    await waitFor(() => {
      expect(screen.getByText(/Lens vs Lyon/)).toBeTruthy();
    });
    expect(screen.getByText("RODANDO")).toBeTruthy();
    expect(screen.getAllByText(/GAP MELHOR PREÇO/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/@Book C/).length).toBeGreaterThan(0);
    expect(screen.getByText("PRÉ-JOGO")).toBeTruthy();
    expect(screen.getAllByText(/FRESCO/).length).toBeGreaterThan(0);
  });

  it("mostra CLV n=0 explicito no detalhe do jogo", async () => {
    render(<LivePage />);
    await waitFor(() => {
      expect(screen.getByText(/Lens vs Lyon/)).toBeTruthy();
    });
    const row = screen.getByText(/Lens vs Lyon/).closest("tr")!;
    row.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    await waitFor(() => {
      expect(screen.getByText(/n = 0/)).toBeTruthy();
    });
    expect(screen.getByText(/NO_MODEL/)).toBeTruthy();
  });
});

describe("useRealtimeStream", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("nao redispara onChange para o mesmo event_id (debounce coalesce rajadas)", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    FakeEventSource.instances = [];
    const onChange = vi.fn();

    function Probe() {
      useRealtimeStream("/api/realtime/stream", onChange);
      return null;
    }
    render(<Probe />);

    const source = FakeEventSource.instances[0];
    source.emit("ODDS_UPDATE", { event_id: "e1" });
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(onChange).toHaveBeenCalledTimes(1);

    // mesmo event_id de novo: dedup, nada dispara
    source.emit("ODDS_UPDATE", { event_id: "e1" });
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(onChange).toHaveBeenCalledTimes(1);

    // evento novo dispara
    source.emit("MOVEMENT", { event_id: "e2" });
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(onChange).toHaveBeenCalledTimes(2);
  });
});
