/**
 * Testes do painel de configuracao do backtest.
 *
 * O ponto critico e a conversao de unidade: a UI edita percentuais (como a
 * GUI legada), mas o contrato da API usa fracao. Um erro aqui mudaria o
 * filtro de EV sem ninguem perceber.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import BacktestConfigPanel from "@/components/backtest/BacktestConfigPanel";
import { backtestOptions } from "@/test/fixtures";
import type { BacktestOptions } from "@/types/backtest";

function setup(overrides: Partial<BacktestOptions> = {}) {
  const onRun = vi.fn();
  const options = { ...backtestOptions, ...overrides };
  render(<BacktestConfigPanel options={options} running={false} onRun={onRun} />);
  return { onRun, options };
}

describe("BacktestConfigPanel", () => {
  it("mostra o cabecalho e o botao de execucao", () => {
    setup();
    expect(screen.getByText("Configuração do teste")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Executar backtest/i }),
    ).toBeInTheDocument();
  });

  it("mostra o periodo disponivel", () => {
    setup();
    expect(screen.getByText(/2025-01-26/)).toBeInTheDocument();
    expect(screen.getByText(/2025-12-14/)).toBeInTheDocument();
  });

  it("lista todos os mercados do motor", () => {
    setup();
    expect(screen.getByText("Resultado Final (1X2)")).toBeInTheDocument();
    expect(screen.getByText("Total de Gols")).toBeInTheDocument();
  });

  it("marca a fonte de odds indisponivel", () => {
    setup();
    expect(screen.getByText(/Odds históricas reais — indisponível/)).toBeInTheDocument();
  });

  it("avisa quando nao ha odds reais importadas", () => {
    setup();
    expect(screen.getByText(/Nenhuma odd real importada/)).toBeInTheDocument();
    expect(screen.getByText("BETGSN_ODDS_API_KEY")).toBeInTheDocument();
  });

  it("mostra o estado do cache quando ha snapshots", () => {
    setup({
      odds_cache: [
        {
          sport_key: "soccer_brazil_campeonato",
          snapshots: 120,
          first: "2025-01-01T00:00:00Z",
          last: "2025-03-01T00:00:00Z",
        },
      ],
    });
    expect(screen.getByText(/120 snapshots reais/)).toBeInTheDocument();
  });

  it("marca o corpus importado como indisponivel quando vazio", () => {
    setup();
    expect(screen.getByText(/Nada importado/)).toBeInTheDocument();
  });

  it("mostra o resumo do corpus importado quando existe", () => {
    const imported = backtestOptions.corpora[1];
    setup({
      corpora: [
        backtestOptions.corpora[0],
        {
          ...imported,
          available: true,
          n_matches: 380,
          first_kickoff: "2024-04-01",
          last_kickoff: "2024-12-08",
          competitions: ["Serie A"],
          fingerprint: "deadbeef",
        },
      ],
    });
    // "380 partidas" aparece no select e no resumo do corpus
    expect(screen.getAllByText(/380 partidas/).length).toBeGreaterThan(0);
    expect(screen.getByText(/fingerprint deadbeef/)).toBeInTheDocument();
  });

  it("converte EV mínimo de % para fração ao executar", async () => {
    const { onRun } = setup();
    const evInput = screen.getByLabelText("EV mínimo");
    await userEvent.clear(evInput);
    await userEvent.type(evInput, "8.5");
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));

    expect(onRun).toHaveBeenCalledTimes(1);
    const request = onRun.mock.calls[0][0];
    expect(request.min_ev).toBeCloseTo(0.085, 6);
  });

  it("converte risco por aposta de % para fração ao executar", async () => {
    const { onRun } = setup();
    const capInput = screen.getByLabelText("Risco por aposta");
    await userEvent.clear(capInput);
    await userEvent.type(capInput, "2.5");
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));

    const request = onRun.mock.calls[0][0];
    expect(request.stake_cap).toBeCloseTo(0.025, 6);
  });

  it("aceita vírgula como separador decimal", async () => {
    const { onRun } = setup();
    const evInput = screen.getByLabelText("EV mínimo");
    await userEvent.clear(evInput);
    await userEvent.type(evInput, "4,5");
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));

    expect(onRun.mock.calls[0][0].min_ev).toBeCloseTo(0.045, 6);
  });

  it("envia os mercados e a fonte de corpus selecionados", async () => {
    const { onRun } = setup();
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));
    const request = onRun.mock.calls[0][0];
    expect(request.market_keys.length).toBeGreaterThan(0);
    expect(request.corpus_source).toBe("local");
    expect(request.odds_source).toBe("naive_synthetic");
  });

  it("permite desmarcar um mercado", async () => {
    const { onRun } = setup();
    await userEvent.click(
      screen.getByText("Total de Gols").closest("label")!.querySelector("input")!,
    );
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));
    const request = onRun.mock.calls[0][0];
    expect(request.market_keys).not.toContain("ou");
    expect(request.market_keys).toContain("1x2");
  });

  it("mostra os limiares do Scanner vindos do backend", () => {
    setup();
    expect(screen.getByText(/FORTE 8,0%/)).toBeInTheDocument();
    expect(screen.getByText(/MÉDIA 4,5%/)).toBeInTheDocument();
    expect(screen.getByText(/FRACA 2,0%/)).toBeInTheDocument();
  });

  it("desabilita o botao enquanto executa", () => {
    render(
      <BacktestConfigPanel options={backtestOptions} running onRun={() => {}} />,
    );
    const button = screen.getByRole("button", { name: /Executando/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("alterna xG e teto de exposicao", async () => {
    const { onRun } = setup();
    const xg = screen.getByLabelText(/Usar xG no ataque/);
    await userEvent.click(xg);
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));
    expect(onRun.mock.calls[0][0].use_xg).toBe(false);
  });

  it("ressincroniza os campos quando as opcoes chegam do backend", async () => {
    const { rerender } = render(
      <BacktestConfigPanel
        options={backtestOptions}
        running={false}
        onRun={() => {}}
      />,
    );
    const next: BacktestOptions = {
      ...backtestOptions,
      default_config: { ...backtestOptions.default_config, min_ev: 0.09 },
    };
    rerender(<BacktestConfigPanel options={next} running={false} onRun={() => {}} />);
    await waitFor(() =>
      expect((screen.getByLabelText("EV mínimo") as HTMLInputElement).value).toBe("9.0"),
    );
  });
});

describe("BacktestConfigPanel — odds reais (football-data.co.uk)", () => {
  it("mostra a fonte de odds reais como disponível", () => {
    setup();
    expect(
      screen.getByText(/Odds reais — football-data.co.uk/),
    ).toBeInTheDocument();
  });

  it("mostra o corpus europeu com a marca de odds reais", () => {
    setup();
    // aparecem no select de corpus e no resumo — por isso getAllByText
    expect(screen.getAllByText(/Ligas europeias/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/104\.177 partidas/).length).toBeGreaterThan(0);
    expect(screen.getByText(/COM odds reais/)).toBeInTheDocument();
  });

  it("só mostra o filtro de bookmakers quando a fonte é o CSV", async () => {
    setup();
    // com a fonte padrão (sintética), não há seleção de book
    expect(screen.queryByText(/Momento da linha/)).not.toBeInTheDocument();

    const select = screen.getByLabelText("Fonte de odds");
    await userEvent.selectOptions(select, "football_data_uk");
    expect(screen.getByText(/Momento da linha/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Pinnacle" })).toBeInTheDocument();
  });

  it("permite escolher abertura ou fechamento", async () => {
    const { onRun } = setup();
    await userEvent.selectOptions(
      screen.getByLabelText("Fonte de odds"),
      "football_data_uk",
    );
    await userEvent.selectOptions(
      screen.getByLabelText("Momento da linha"),
      "opening",
    );
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));

    const request = onRun.mock.calls[0][0];
    expect(request.odds_source).toBe("football_data_uk");
    expect(request.odds_closing).toBe(false);
  });

  it("permite restringir a Pinnacle", async () => {
    const { onRun } = setup();
    await userEvent.selectOptions(
      screen.getByLabelText("Fonte de odds"),
      "football_data_uk",
    );
    await userEvent.click(screen.getByRole("checkbox", { name: "Pinnacle" }));
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));

    expect(onRun.mock.calls[0][0].odds_books).toEqual(["Pinnacle"]);
  });

  it("por padrão envia a lista de books vazia (usa todas as casas)", async () => {
    const { onRun } = setup();
    await userEvent.selectOptions(
      screen.getByLabelText("Fonte de odds"),
      "football_data_uk",
    );
    await userEvent.click(screen.getByRole("button", { name: /Executar backtest/i }));
    expect(onRun.mock.calls[0][0].odds_books).toEqual([]);
  });
});
