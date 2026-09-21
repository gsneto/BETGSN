/** Endpoints de movimento de odds. */
import { apiGet } from "./client";
import type { OddsMovementOverview } from "@/types/api";

export function fetchMovement(signal?: AbortSignal): Promise<OddsMovementOverview> {
  return apiGet<OddsMovementOverview>("/api/movement", undefined, { signal });
}
