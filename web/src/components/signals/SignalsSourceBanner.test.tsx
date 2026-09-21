/**
 * Testes do aviso de origem dos sinais.
 *
 * Este componente é um contrato de honestidade: o usuário precisa saber
 * quando os sinais vêm de jogos inventados, e quando o EV exibido é
 * inflado pelo viés medido do modelo.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import SignalsSourceBanner from "@/components/signals/SignalsSourceBanner";
import { betDecisionNoBet } from "@/test/fixtures";
import type { BetDecision, SignalsSourceStatus } from "@/types/api";

const status: SignalsSourceStatus = {
  real: {
    available: true,
    n_fixtures: 320,
    with_odds: 320,
    competitions: ["Premier League (England)"],
    first_date: "2026-09-18",
    last_date: "2026-09-22",
  },
  synthetic: { available: true },
  default_source: "real",
};

const calibration = {
  measured_on: "13.334 partidas (top-5 europeu, 2018-2025)",
  ev_predicted: 0.224,
  return_realized: -0.017,
  gap_pp: 0.241,
  simulated_roi: -0.055,
  verdict: "O modelo é sistematicamente superconfiante.",
};

function setup(source: "real" | "synthetic" = "real", decision: BetDecision | null = betDecisionNoBet) {
  const onChange = vi.fn();
  render(
    <SignalsSourceBanner
      source={source}
      status={status}
      detail="fonte de teste"
      calibration={source === "real" ? calibration : null}
      skippedNoRating={18}
      decision={decision}
      onChange={onChange}
    />,
  );
  return { onChange };
}

describe("SignalsSourceBanner — origem", () => {
  it("identifica a fonte real", () => {
    setup("real");
    expect(screen.getByText("Dados reais")).toBeInTheDocument();
    expect(screen.getByText("jogos + odds reais")).toBeInTheDocument();
  });

  it("identifica o dataset sintético como não utilizável", () => {
    setup("synthetic");
    expect(screen.getByText("Dataset sintético")).toBeInTheDocument();
    expect(screen.getByText("não use para apostar")).toBeInTheDocument();
  });

  it("mostra a contagem de jogos reais disponíveis", () => {
    setup("real");
    expect(screen.getByText(/320 jogos/)).toBeInTheDocument();
  });

  it("troca de fonte ao clicar", async () => {
    const { onChange } = setup("real");
    await userEvent.click(screen.getByRole("button", { name: "Sintéticos" }));
    expect(onChange).toHaveBeenCalledWith("synthetic");
  });

  it("desabilita a fonte real quando indisponível", () => {
    render(
      <SignalsSourceBanner
        source="synthetic"
        status={{ ...status, real: { available: false, n_fixtures: 0 } }}
        detail=""
        calibration={null}
        skippedNoRating={0}
        decision={null}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Reais" })).toBeDisabled();
  });
});

describe("SignalsSourceBanner — aviso do sintético", () => {
  it("avisa que os jogos não são reais", () => {
    setup("synthetic");
    expect(
      screen.getByText(/não correspondem a jogos reais/i),
    ).toBeInTheDocument();
  });

  it("explica que as datas estão fixas no código", () => {
    setup("synthetic");
    expect(screen.getByText(/betgsn\/data\.py/)).toBeInTheDocument();
  });

  it("aponta a aba Reais como alternativa", () => {
    setup("synthetic");
    // "Reais" aparece no botao do seletor
    expect(screen.getByRole("button", { name: "Reais" })).toBeInTheDocument();
  });
});

describe("SignalsSourceBanner — viés medido do modelo", () => {
  it("avisa que o EV não é confiável mesmo com dados reais", () => {
    setup("real");
    expect(
      screen.getByText(/O EV não é confiável/i),
    ).toBeInTheDocument();
  });

  it("mostra o gap medido em pontos percentuais", () => {
    setup("real");
    expect(screen.getByText(/\+24\.1pp/)).toBeInTheDocument();
  });

  it("mostra o EV previsto e o realizado", () => {
    setup("real");
    expect(screen.getByText(/22,4%/)).toBeInTheDocument();
    // o formatador usa hifen ASCII (Intl pt-BR), nao o sinal de menos Unicode
    expect(screen.getByText(/-1,7%/)).toBeInTheDocument();
  });

  it("aponta a estratégia validada como alternativa", () => {
    setup("real");
    expect(screen.getByText(/--scan-value/)).toBeInTheDocument();
  });

  it("informa quantos jogos foram descartados sem rating", () => {
    setup("real");
    expect(screen.getByText(/18 jogos futuros foram descartados/)).toBeInTheDocument();
  });

  it("não mostra o aviso de viés no modo sintético", () => {
    setup("synthetic");
    expect(screen.queryByText(/O EV não é confiável/i)).not.toBeInTheDocument();
  });
});

describe("SignalsSourceBanner — decisão do Quant (BET | NO_BET)", () => {
  it("exibe NO_BET como resultado de primeira classe", () => {
    setup("real", betDecisionNoBet);
    expect(
      screen.getByText(/Decisão do Quant: NÃO APOSTAR/i),
    ).toBeInTheDocument();
    expect(screen.getByTestId("quant-decision")).toHaveAttribute(
      "data-decision",
      "NO_BET",
    );
  });

  it("preserva o motivo da decisão vindo do backend", () => {
    setup("real", betDecisionNoBet);
    expect(screen.getByTestId("quant-decision-reason")).toHaveTextContent(
      "falhou: evidencia_confiavel",
    );
  });

  it("expõe qual verificação falhou, com o detalhe", () => {
    renderBannerOnlyDecision();
    const panel = screen.getByTestId("quant-decision");
    expect(panel.textContent).toContain("evidencia_confiavel");
    expect(panel.textContent).toContain("odds sem timestamp/validacao");
  });

  it("não cria aposta nem stake para NO_BET", () => {
    const { container } = renderBannerOnlyDecision();
    // o painel de decisão não contém botão de aposta nem valor de stake
    const panel = screen.getByTestId("quant-decision");
    expect(panel.querySelector("button")).toBeNull();
    expect(panel.textContent).not.toMatch(/stake|fracao da banca|aposte agora/i);
    expect(container.textContent).not.toMatch(/stake/i);
  });

  it("distingue BET de NO_BET", () => {
    const bet: BetDecision = {
      action: "BET",
      reason: "vantagem conservadora +0,71%, Kelly 7,62%, fracao 1,91%",
      fraction: 0.0191,
      conservative_roi: 0.0071,
      kelly_full: 0.0762,
      checks: betDecisionNoBet.checks.map((c) => ({ ...c, passed: true })),
    };
    setup("real", bet);
    expect(
      screen.getByText(/Decisão do Quant: APOSTAR/i),
    ).toBeInTheDocument();
    expect(screen.getByTestId("quant-decision")).toHaveAttribute(
      "data-decision",
      "BET",
    );
    // no NO_BET o painel avisa que nenhuma aposta e criada; no BET nao
    expect(
      screen.queryByText(/Nenhuma aposta é criada/i),
    ).not.toBeInTheDocument();
  });

  it("sem decisão (backend antigo), não renderiza o painel", () => {
    setup("real", null);
    expect(screen.queryByTestId("quant-decision")).not.toBeInTheDocument();
  });
});

function renderBannerOnlyDecision() {
  return render(
    <SignalsSourceBanner
      source="real"
      status={status}
      detail="fonte de teste"
      calibration={null}
      skippedNoRating={0}
      decision={betDecisionNoBet}
      onChange={() => {}}
    />,
  );
}
