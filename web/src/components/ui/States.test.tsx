/**
 * Testes do frontend: loading/error/empty states.
 */

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import {
  EmptyState,
  ErrorPanel,
  Skeleton,
  KpiSkeleton,
  TableSkeleton,
} from "@/components/ui/States";
import { ApiError } from "@/api/client";

describe("States", () => {
  it("renderiza Skeleton", () => {
    render(<Skeleton className="h-4 w-full" />);
    const el = document.querySelector(".skeleton");
    expect(el).toBeInTheDocument();
  });

  it("renderiza KpiSkeleton", () => {
    render(<KpiSkeleton count={4} />);
    expect(document.querySelectorAll(".panel").length).toBeGreaterThanOrEqual(4);
  });

  it("renderiza TableSkeleton", () => {
    render(<TableSkeleton rows={3} cols={4} />);
    expect(document.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
  });

  it("renderiza EmptyState com título e hint", () => {
    render(
      <EmptyState
        title="Nenhum dado"
        hint="Ainda sem dados."
      />,
    );
    expect(screen.getByText("Nenhum dado")).toBeInTheDocument();
    expect(screen.getByText("Ainda sem dados.")).toBeInTheDocument();
  });

  it("renderiza ErrorPanel com mensagem e botão", () => {
    render(
      <ErrorPanel
        message="Falha ao carregar"
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText("Falha ao carregar")).toBeInTheDocument();
    expect(screen.getByText("Tentar novamente")).toBeInTheDocument();
  });

  it("renderiza ErrorPanel sem botão quando onRetry indefinido", () => {
    render(<ErrorPanel message="Erro" />);
    expect(screen.getByText("Erro")).toBeInTheDocument();
  });
});

describe("ApiError", () => {
  it("devolve mensagem de rede para status 0", () => {
    const err = new ApiError(0, "timeout");
    expect(err.userMessage).toContain("Backend BETGSN indisponivel");
  });

  it("devolve mensagem estruturada para 503", () => {
    const err = new ApiError(503, "pipeline indisponivel");
    expect(err.userMessage).toContain("Pipeline");
  });
});
