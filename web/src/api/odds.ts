/** Endpoints da tela CASAS / ODDS. */

import { apiGet } from "./client";
import type { MarketComparison, OddsOverview } from "@/types/api";

export function fetchOddsOverview(signal?: AbortSignal): Promise<OddsOverview> {
  return apiGet<OddsOverview>("/api/odds", undefined, { signal });
}

export function fetchMarketComparison(
  match: string,
  market?: string,
  signal?: AbortSignal,
): Promise<MarketComparison> {
  return apiGet<MarketComparison>(
    "/api/odds/comparison",
    { match, market },
    { signal },
  );
}
