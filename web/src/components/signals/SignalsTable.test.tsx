/**
 * SignalsTable — contrato NO_BET.
 *
 * Sob `betAllowed=false`, a tabela NÃO pode apresentar valor operacional
 * (stake/% banca/lucro) nem o rótulo "Aposta": a decisão global já
 * rejeitou a aposta, então a superfície é apenas triagem analítica.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SignalsTable from "@/components/signals/SignalsTable";
import type { Signal } from "@/types/api";

function signal(overrides: Partial<Signal> = {}): Signal {
  return {
    id: "s1",
    match: "Lens vs Lyon",
    home: "Lens",
    away: "Lyon",
    kickoff: "2026-09-28",
    league: "Ligue 1",
    round_label: "Rodada 5",
    market: "Resultado Final (1X2)",
    outcome: "Lens",
    best_odd: 2.3,
    best_book: "Pinnacle",
    median_odd: 2.2,
    fair_odd: 2.1,
    n_books: 3,
    model_prob: 0.5,
    market_prob: 0.45,
    edge: 0.05,
    ev: 0.15,
    kelly: 0.05,
    stake: 1.82,
    stake_pct: 0.00182,
    expected_profit: 0.21,
    expected_profit_pct: 0.0002,
    gross_profit_if_win: 2.37,
    loss_if_lose: 1.82,
    confidence: "MEDIA",
    rationale: "consenso amplo",
    ...overrides,
  };
}

describe("SignalsTable", () => {
  it("sob BET mostra coluna Aposta e valor de stake", () => {
    render(
      <SignalsTable rows={[signal()]} maxEv={0.15} hasFilters={false} betAllowed />,
    );
    expect(screen.getByText("Aposta")).toBeInTheDocument();
    // stake formatado (moeda) presente
    expect(screen.getByText(/1,82/)).toBeInTheDocument();
  });

  it("sob NO_BET não rotula Aposta nem apresenta stake", () => {
    render(
      <SignalsTable
        rows={[signal({ stake: 0, stake_pct: 0, expected_profit: 0, gross_profit_if_win: 0 })]}
        maxEv={0.15}
        hasFilters={false}
        betAllowed={false}
      />,
    );
    expect(screen.queryByText("Aposta")).not.toBeInTheDocument();
    expect(screen.getByText("Resultado")).toBeInTheDocument();
    // Stake zerado não é exibido como valor operacional: aparece "—".
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });
});
