/**
 * Testes dos primitivos de UI.
 *
 * Foco no que e contrato, nao em detalhe de estilo: tom semantico da
 * confianca, fidelidade das barras de probabilidade e estado do segmented
 * control.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import Badge from "@/components/ui/Badge";
import { confidenceTone } from "@/components/ui/badgeTone";
import { CalibrationChart, LineChart } from "@/components/ui/Charts";
import KpiCard from "@/components/ui/KpiCard";
import ProbBar, { ProbCompareBars } from "@/components/ui/ProbBar";
import SegmentedControl from "@/components/ui/SegmentedControl";
import { EmptyState, ErrorPanel } from "@/components/ui/States";

describe("Badge", () => {
  it("renderiza o conteudo", () => {
    render(<Badge>FORTE</Badge>);
    expect(screen.getByText("FORTE")).toBeInTheDocument();
  });

  it("mostra o dot quando pedido", () => {
    const { container } = render(<Badge dot>FORTE</Badge>);
    const dot = container.querySelector('[aria-hidden="true"]');
    expect(dot).not.toBeNull();
  });

  it("nao mostra dot por padrao", () => {
    const { container } = render(<Badge>FRACA</Badge>);
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
  });
});

describe("confidenceTone", () => {
  it("mapeia cada nivel do backend para um tom semantico", () => {
    expect(confidenceTone("FORTE")).toBe("accent");
    expect(confidenceTone("MEDIA")).toBe("info");
    expect(confidenceTone("FRACA")).toBe("neutral");
    expect(confidenceTone("DESCARTE")).toBe("neutral");
    expect(confidenceTone("desconhecido")).toBe("neutral");
  });
});

describe("ProbBar", () => {
  it("a largura representa fielmente o valor", () => {
    const { container } = render(<ProbBar value={0.916} />);
    const fill = container.querySelector("span > span") as HTMLElement;
    expect(fill.style.width).toBe("91.6%");
  });

  it("valores pequenos continuam pequenos (sem exagero enganoso)", () => {
    const { container } = render(<ProbBar value={0.05} />);
    const fill = container.querySelector("span > span") as HTMLElement;
    expect(fill.style.width).toBe("5%");
  });

  it("expoe o valor via role=meter para acessibilidade", () => {
    render(<ProbBar value={0.5} />);
    const meter = screen.getByRole("meter");
    expect(meter).toHaveAttribute("aria-valuenow", "50");
    expect(meter).toHaveAttribute("aria-valuemin", "0");
    expect(meter).toHaveAttribute("aria-valuemax", "100");
  });

  it("limita valores fora do intervalo", () => {
    const { container: over } = render(<ProbBar value={1.5} />);
    expect((over.querySelector("span > span") as HTMLElement).style.width).toBe("100%");
    const { container: under } = render(<ProbBar value={-0.2} />);
    expect((under.querySelector("span > span") as HTMLElement).style.width).toBe("0%");
  });
});

describe("ProbCompareBars", () => {
  it("mostra modelo e mercado na mesma escala", () => {
    render(<ProbCompareBars model={0.9} market={0.14} />);
    const meters = screen.getAllByRole("meter");
    expect(meters).toHaveLength(2);
    expect(meters[0]).toHaveAttribute("aria-valuenow", "90");
    expect(meters[1]).toHaveAttribute("aria-valuenow", "14");
  });
});

describe("SegmentedControl", () => {
  const segments = [
    { value: "all" as const, label: "Todos", count: 167 },
    { value: "FORTE" as const, label: "Forte", count: 10 },
  ];

  it("marca o segmento ativo", () => {
    render(
      <SegmentedControl segments={segments} value="all" onChange={() => {}} />,
    );
    expect(screen.getByRole("radio", { name: /Todos/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("radio", { name: /Forte/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("dispara onChange ao clicar", async () => {
    const onChange = vi.fn();
    render(
      <SegmentedControl segments={segments} value="all" onChange={onChange} />,
    );
    await userEvent.click(screen.getByRole("radio", { name: /Forte/ }));
    expect(onChange).toHaveBeenCalledWith("FORTE");
  });

  it("mostra as contagens", () => {
    render(
      <SegmentedControl segments={segments} value="all" onChange={() => {}} />,
    );
    expect(screen.getByText("167")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
  });
});

describe("KpiCard", () => {
  it("mostra label, valor e contexto", () => {
    render(
      <KpiCard label="Sinais fortes" value="10" context="6% da lista" />,
    );
    expect(screen.getByText("Sinais fortes")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("6% da lista")).toBeInTheDocument();
  });

  it("mostra o delta quando informado", () => {
    render(
      <KpiCard
        label="Lucro esperado"
        value="+11,63"
        delta={{ text: "+1,16%", tone: "positive" }}
      />,
    );
    expect(screen.getByText("+1,16%")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("mostra titulo, dica e acao", () => {
    render(
      <EmptyState
        title="Nenhum sinal encontrado"
        hint="Ajuste os filtros."
        action={<button type="button">Limpar</button>}
      />,
    );
    expect(screen.getByText("Nenhum sinal encontrado")).toBeInTheDocument();
    expect(screen.getByText("Ajuste os filtros.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Limpar" })).toBeInTheDocument();
  });
});

describe("ErrorPanel", () => {
  it("tem role=alert e permite tentar de novo", async () => {
    const onRetry = vi.fn();
    render(<ErrorPanel message="Backend fora do ar" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Backend fora do ar")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Tentar novamente/ }));
    expect(onRetry).toHaveBeenCalled();
  });
});

describe("CalibrationChart", () => {
  it("mostra aviso quando nao ha bins", () => {
    render(<CalibrationChart bins={[]} />);
    expect(screen.getByText("Sem bins de calibração")).toBeInTheDocument();
  });

  it("renderiza um ponto por faixa com descricao acessivel", () => {
    const { container } = render(
      <CalibrationChart
        bins={[
          { predicted: 0.57, empirical: 0.51, n: 544 },
          { predicted: 0.92, empirical: 0.9, n: 195 },
        ]}
      />,
    );
    const circles = container.querySelectorAll("circle");
    expect(circles).toHaveLength(2);
    expect(container.querySelector("title")?.textContent).toContain("previsto 57%");
  });
});

describe("LineChart", () => {
  it("mostra aviso quando nao ha dados", () => {
    render(<LineChart series={[{ name: "x", values: [] }]} labels={[]} />);
    expect(screen.getByText("Sem dados")).toBeInTheDocument();
  });

  it("desenha uma polyline por serie", () => {
    const { container } = render(
      <LineChart
        series={[
          { name: "Capital", values: [1000, 1100, 1050], tone: "accent" },
          { name: "Drawdown", values: [0, 0, -0.05], tone: "negative", dashed: true },
        ]}
        labels={["2025-04-06", "2025-04-13", "2025-04-20"]}
        referenceValue={1000}
      />,
    );
    expect(container.querySelectorAll("polyline")).toHaveLength(2);
    // a linha de referencia (banca inicial) tambem aparece
    expect(container.querySelectorAll("line[stroke-dasharray]").length).toBeGreaterThan(0);
  });
});
