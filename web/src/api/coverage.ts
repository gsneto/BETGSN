/** Endpoints de coverage. */
import { apiGet } from "./client";
import type { CoverageReport } from "@/types/api";

export function fetchCoverage(signal?: AbortSignal): Promise<CoverageReport> {
  return apiGet<CoverageReport>("/api/coverage", undefined, { signal });
}
