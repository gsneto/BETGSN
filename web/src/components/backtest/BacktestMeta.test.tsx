/**
 * Testes dos avisos do backtest.
 *
 * O `BaselineWarning` é um contrato de honestidade: enquanto o mercado de
 * referência for o baseline ingênuo, a tela precisa deixar explícito que a
 * simulação não é estimativa de lucro. Estes testes travam isso.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BacktestProgress,
  BaselineWarning,
  MissingDataNotice,
} from "@/components/backtest/BacktestMeta";
import { backtestJobStatus, backtestOptions } from "@/test/fixtures";

describe("BaselineWarning", () => {
  it("avisa quando o mercado é o baseline ingênuo", () => {
    render(
      <BaselineWarning
        oddsSource="naive_synthetic"
        avgModelProb={0.59}
        avgMarketProb={0.175}
      />,
    );
    expect(
      screen.getByText(/NÃO é estimativa de lucro/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("mostra o gap entre modelo e mercado como evidência", () => {
    render(
      <BaselineWarning
        oddsSource="naive_synthetic"
        avgModelProb={0.59}
        avgMarketProb={0.175}
      />,
    );
    expect(screen.getByText(/59,0%/)).toBeInTheDocument();
    expect(screen.getByText(/17,5%/)).toBeInTheDocument();
    expect(screen.getByText(/41,5%/)).toBeInTheDocument();
  });

  it("diz o que É e o que NÃO é válido", () => {
    render(
      <BaselineWarning
        oddsSource="naive_synthetic"
        avgModelProb={0.5}
        avgMarketProb={0.2}
      />,
    );
    expect(screen.getByText(/calibração/i)).toBeInTheDocument();
    expect(screen.getByText(/Brier/)).toBeInTheDocument();
    expect(screen.getByText(/capital final, ROI e drawdown/i)).toBeInTheDocument();
  });

  it("aponta a saída (captura de odds ao vivo)", () => {
    render(
      <BaselineWarning
        oddsSource="naive_synthetic"
        avgModelProb={0.5}
        avgMarketProb={0.2}
      />,
    );
    expect(screen.getByText(/--capture-odds/)).toBeInTheDocument();
  });

  it("não aparece quando as odds são reais", () => {
    const { container } = render(
      <BaselineWarning
        oddsSource="real_historical"
        avgModelProb={0.5}
        avgMarketProb={0.48}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("BacktestProgress", () => {
  it("não aparece quando ocioso", () => {
    const { container } = render(
      <BacktestProgress status={{ ...backtestJobStatus, phase: "idle", done: 0 }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("mostra fase, contagem e percentual", () => {
    render(<BacktestProgress status={backtestJobStatus} />);
    expect(screen.getByText("Analisando partidas (50/100)")).toBeInTheDocument();
    expect(screen.getByText(/Analisando partidas · 50\/100 · 50%/)).toBeInTheDocument();
  });

  it("expõe o erro quando a execução falha", () => {
    render(
      <BacktestProgress
        status={{
          ...backtestJobStatus,
          phase: "error",
          error: "ValueError: mercado desconhecido",
          message: "Configuração inválida.",
        }}
      />,
    );
    expect(screen.getByText("Configuração inválida.")).toBeInTheDocument();
    expect(screen.getByText(/mercado desconhecido/)).toBeInTheDocument();
  });

  it("marca o fim como concluído", () => {
    render(
      <BacktestProgress
        status={{
          ...backtestJobStatus,
          phase: "done",
          progress: 1,
          message: "Concluído: 100 sinais em 50 partidas.",
        }}
      />,
    );
    expect(screen.getByText(/Concluído: 100 sinais/)).toBeInTheDocument();
  });
});

describe("MissingDataNotice", () => {
  it("lista cada dado ausente com motivo, fonte e como plugar", () => {
    render(<MissingDataNotice notes={backtestOptions.missing_data_notes} />);
    expect(screen.getByText("odds historicas reais")).toBeInTheDocument();
    expect(screen.getByText("The Odds API")).toBeInTheDocument();
    expect(screen.getByText("BETGSN_ODDS_API_KEY")).toBeInTheDocument();
  });

  it("não renderiza nada sem notas", () => {
    const { container } = render(<MissingDataNotice notes={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
