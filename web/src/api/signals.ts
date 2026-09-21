/** Endpoints da tela SINAIS. */

import { apiGet } from "./client";
import type { SignalReport, SignalSource, SignalsSourceStatus } from "@/types/api";

/**
 * Relatorio de sinais.
 *
 * `source="real"` usa jogos futuros reais com odds reais
 * (football-data.co.uk). `source="synthetic"` usa o dataset local gerado
 * em memoria — datas fixas no codigo, odds do proprio modelo.
 */
export function fetchSignals(
  source: SignalSource = "real",
  params: { minEv?: number; bankroll?: number; useXg?: boolean } = {},
  signal?: AbortSignal,
): Promise<SignalReport> {
  return apiGet<SignalReport>(
    "/api/signals",
    {
      source,
      min_ev: params.minEv,
      bankroll: params.bankroll,
      use_xg: params.useXg,
    },
    { signal, timeoutMs: 90_000 },
  );
}

/** Disponibilidade das duas fontes, para a UI escolher o padrao. */
export function fetchSignalsStatus(signal?: AbortSignal): Promise<SignalsSourceStatus> {
  return apiGet<SignalsSourceStatus>("/api/signals/status", undefined, {
    signal,
    timeoutMs: 15_000,
  });
}
