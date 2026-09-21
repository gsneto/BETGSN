/** Endpoints de sistema, dashboard e recalculo. */

import { apiGet, apiPost } from "./client";
import type {
  DashboardSummary,
  ModelConfiguration,
  SystemStatus,
} from "@/types/api";

export function fetchHealth(signal?: AbortSignal): Promise<SystemStatus> {
  return apiGet<SystemStatus>("/api/health", undefined, {
    signal,
    timeoutMs: 6_000,
  });
}

export function fetchDashboard(signal?: AbortSignal): Promise<DashboardSummary> {
  return apiGet<DashboardSummary>("/api/dashboard", undefined, { signal });
}

export function fetchConfig(signal?: AbortSignal): Promise<ModelConfiguration> {
  return apiGet<ModelConfiguration>("/api/config", undefined, { signal });
}

/** Dispara o pipeline no backend. Toda a estatistica acontece la. */
export function postRecalculate(
  config: ModelConfiguration,
  signal?: AbortSignal,
): Promise<DashboardSummary> {
  return apiPost<DashboardSummary>("/api/recalculate", config, {
    signal,
    timeoutMs: 60_000,
  });
}
