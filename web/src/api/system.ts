/** Endpoints de sistema, dashboard e recalculo. */

import { apiGet, apiPost } from "./client";
import type {
  DashboardSummary,
  ModelConfiguration,
  RecalculateJobStatus,
} from "@/types/api";

export function fetchDashboard(signal?: AbortSignal): Promise<DashboardSummary> {
  return apiGet<DashboardSummary>("/api/dashboard", undefined, { signal });
}

/**
 * Dispara o pipeline no backend como JOB ASSINCRONO.
 *
 * O POST retorna assim que o job e aceito (nao quando o pipeline
 * termina): o progresso chega via WebSocket (`recalculate:progress`) ou
 * por `fetchRecalculateStatus`. Toda a estatistica acontece no backend.
 */
export function postRecalculate(
  config: ModelConfiguration,
  signal?: AbortSignal,
): Promise<RecalculateJobStatus> {
  return apiPost<RecalculateJobStatus>("/api/recalculate", config, {
    signal,
    // so o aceite do job — o pipeline em si e acompanhado por
    // WebSocket/polling, nunca por uma requisicao longa
    timeoutMs: 15_000,
  });
}

/** Progresso do job de recalculo corrente (ou do ultimo concluido). */
export function fetchRecalculateStatus(
  signal?: AbortSignal,
): Promise<RecalculateJobStatus> {
  return apiGet<RecalculateJobStatus>(
    "/api/recalculate/status",
    undefined,
    { signal, timeoutMs: 10_000 },
  );
}

/** Cancelamento cooperativo: aplicado entre fases do job. */
export function postRecalculateCancel(
  signal?: AbortSignal,
): Promise<RecalculateJobStatus> {
  return apiPost<RecalculateJobStatus>("/api/recalculate/cancel", {}, {
    signal,
    timeoutMs: 10_000,
  });
}
