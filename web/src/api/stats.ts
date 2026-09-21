/** Endpoint da tela ESTATISTICAS. */

import { apiGet } from "./client";
import type { StatsOverview } from "@/types/api";

export function fetchStats(signal?: AbortSignal): Promise<StatsOverview> {
  return apiGet<StatsOverview>("/api/stats", undefined, { signal });
}
