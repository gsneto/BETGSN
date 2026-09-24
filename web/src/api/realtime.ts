/**
 * Endpoints do terminal de mercado em tempo real.
 *
 * O SSE (useRealtimeStream) e so o gatilho de "algo mudou": os DADOS
 * sempre vem dos endpoints tipados daqui — sem parse de payload no hook.
 */
import { apiGet } from "./client";
import type {
  RealtimeBoard,
  RealtimeMatchDetail,
  RealtimeProvidersResponse,
  RealtimeSignalsResponse,
  RealtimeStatusResponse,
} from "@/types/realtime";

export function fetchRealtimeStatus(signal?: AbortSignal): Promise<RealtimeStatusResponse> {
  return apiGet<RealtimeStatusResponse>("/api/realtime/status", undefined, { signal });
}

export function fetchRealtimeBoard(
  params?: { market?: string; signal_type?: string; min_books?: number },
  signal?: AbortSignal,
): Promise<RealtimeBoard> {
  return apiGet<RealtimeBoard>("/api/realtime/board", params, { signal });
}

export function fetchRealtimeMatch(
  eventKey: string,
  signal?: AbortSignal,
): Promise<RealtimeMatchDetail> {
  return apiGet<RealtimeMatchDetail>(
    "/api/realtime/match",
    { event_key: eventKey },
    { signal },
  );
}

export function fetchRealtimeSignals(
  params?: { event_key?: string; signal_type?: string },
  signal?: AbortSignal,
): Promise<RealtimeSignalsResponse> {
  return apiGet<RealtimeSignalsResponse>("/api/realtime/signals", params, { signal });
}

export function fetchRealtimeProviders(signal?: AbortSignal): Promise<RealtimeProvidersResponse> {
  return apiGet<RealtimeProvidersResponse>("/api/realtime/providers", undefined, { signal });
}

export function startRealtimeEngine(): Promise<{ started: boolean }> {
  return apiGet<{ started: boolean }>("/api/realtime/start");
}

export function stopRealtimeEngine(): Promise<{ stopped: boolean }> {
  return apiGet<{ stopped: boolean }>("/api/realtime/stop");
}
