/** Endpoint da tela JOGOS. */

import { apiGet } from "./client";
import type { GameAnalysis } from "@/types/api";

export function fetchGames(signal?: AbortSignal): Promise<GameAnalysis[]> {
  return apiGet<GameAnalysis[]>("/api/games", undefined, { signal });
}
