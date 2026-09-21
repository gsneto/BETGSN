/** Endpoints da tela PORTFOLIO. */

import { apiGet } from "./client";
import type { ExposureReport, ParlayCandidate } from "@/types/portfolio";

export interface BestParlaysParams {
  max_legs?: number;
  min_ev?: number;
  max_same_match?: number;
}

export function fetchBestParlays(
  params: BestParlaysParams,
  signal?: AbortSignal,
): Promise<ParlayCandidate[]> {
  return apiGet<ParlayCandidate[]>("/api/portfolio/best-parlays", { ...params }, { signal });
}

export function fetchExposure(signal?: AbortSignal): Promise<ExposureReport> {
  return apiGet<ExposureReport>("/api/portfolio/exposure", undefined, { signal });
}
