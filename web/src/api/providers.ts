/** Endpoints de providers e health. */
import { apiGet } from "./client";
import type { ProviderOverview } from "@/types/api";

export function fetchProviders(signal?: AbortSignal): Promise<ProviderOverview> {
  return apiGet<ProviderOverview>("/api/providers", undefined, { signal });
}
