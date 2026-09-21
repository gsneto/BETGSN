/** Endpoints da tela MODELO. */

import { apiGet } from "./client";
import type { ModelPerformance, ProbabilityModel } from "@/types/api";

export function fetchModel(signal?: AbortSignal): Promise<ProbabilityModel> {
  return apiGet<ProbabilityModel>("/api/model", undefined, { signal });
}

/** Backtest do backend (calibracao + simulacao). Pode levar segundos. */
export function fetchModelPerformance(
  params: { split?: number; bankroll?: number; min_ev?: number } = {},
  signal?: AbortSignal,
): Promise<ModelPerformance> {
  return apiGet<ModelPerformance>("/api/model/performance", params, {
    signal,
    timeoutMs: 90_000,
  });
}
