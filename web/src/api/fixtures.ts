/** Endpoints de fixtures. */
import { apiGet } from "./client";
import type { FixtureOverview } from "@/types/api";

export function fetchFixtures(signal?: AbortSignal): Promise<FixtureOverview> {
  return apiGet<FixtureOverview>("/api/fixtures", undefined, { signal });
}
