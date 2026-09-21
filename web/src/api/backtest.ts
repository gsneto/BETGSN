/** Endpoints do modulo de BACKTEST. */

import { apiDelete, apiGet, apiPost } from "./client";
import type {
  BacktestJobStatus,
  BacktestOptions,
  BacktestRequest,
  BacktestRunDetail,
  BacktestRunSummary,
  RunComparison,
  SignalPage,
} from "@/types/backtest";

export function fetchBacktestOptions(signal?: AbortSignal): Promise<BacktestOptions> {
  return apiGet<BacktestOptions>("/api/backtest/options", undefined, { signal });
}

export function fetchBacktestStatus(signal?: AbortSignal): Promise<BacktestJobStatus> {
  return apiGet<BacktestJobStatus>("/api/backtest/status", undefined, {
    signal,
    timeoutMs: 8_000,
  });
}

/** Dispara o backtest. O processamento acontece inteiro no backend. */
export function startBacktest(
  request: BacktestRequest,
  signal?: AbortSignal,
): Promise<BacktestJobStatus> {
  return apiPost<BacktestJobStatus>("/api/backtest/run", request, {
    signal,
    timeoutMs: 20_000,
  });
}

export function fetchBacktestRuns(
  limit = 50,
  signal?: AbortSignal,
): Promise<BacktestRunSummary[]> {
  return apiGet<BacktestRunSummary[]>("/api/backtest/runs", { limit }, { signal });
}

export function fetchBacktestRun(
  runId: string,
  signal?: AbortSignal,
): Promise<BacktestRunDetail> {
  return apiGet<BacktestRunDetail>(`/api/backtest/runs/${runId}`, undefined, { signal });
}

export interface SignalQuery {
  search?: string;
  market?: string;
  confidence?: string;
  outcome_result?: string;
  offset?: number;
  limit?: number;
}

export function fetchBacktestSignals(
  runId: string,
  query: SignalQuery = {},
  signal?: AbortSignal,
): Promise<SignalPage> {
  return apiGet<SignalPage>(
    `/api/backtest/runs/${runId}/signals`,
    {
      search: query.search,
      market: query.market,
      confidence: query.confidence,
      outcome_result: query.outcome_result,
      offset: query.offset ?? 0,
      limit: query.limit ?? 100,
    },
    { signal },
  );
}

export function deleteBacktestRun(
  runId: string,
  signal?: AbortSignal,
): Promise<{ deleted: boolean }> {
  return apiDelete<{ deleted: boolean }>(`/api/backtest/runs/${runId}`, { signal });
}

export function compareBacktestRuns(
  a: string,
  b: string,
  signal?: AbortSignal,
): Promise<RunComparison> {
  return apiGet<RunComparison>("/api/backtest/compare", { a, b }, { signal });
}
