/** Endpoints de CLV. */
import { apiGet } from "./client";
import type { ClvReport } from "@/types/api";

export function fetchClv(signal?: AbortSignal): Promise<ClvReport> {
  return apiGet<ClvReport>("/api/clv", undefined, { signal });
}
