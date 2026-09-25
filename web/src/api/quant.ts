/** Endpoints de observabilidade quantitativa (/api/quant). */
import { apiGet } from "./client";
import type {
  QuantBenchmarkManifest,
  QuantClvStatus,
  QuantLineShopping,
  QuantMl,
  QuantModelVsMarket,
} from "@/types/quant";

export function fetchQuantBenchmarks(signal?: AbortSignal): Promise<QuantBenchmarkManifest> {
  return apiGet<QuantBenchmarkManifest>("/api/quant/benchmarks", undefined, { signal });
}

export function fetchQuantModelVsMarket(signal?: AbortSignal): Promise<QuantModelVsMarket> {
  return apiGet<QuantModelVsMarket>("/api/quant/model-vs-market", undefined, { signal });
}

export function fetchQuantLineShopping(signal?: AbortSignal): Promise<QuantLineShopping> {
  return apiGet<QuantLineShopping>("/api/quant/line-shopping", undefined, { signal });
}

export function fetchQuantMl(signal?: AbortSignal): Promise<QuantMl> {
  return apiGet<QuantMl>("/api/quant/ml", undefined, { signal });
}

export function fetchQuantClvStatus(signal?: AbortSignal): Promise<QuantClvStatus> {
  return apiGet<QuantClvStatus>("/api/quant/clv/status", undefined, { signal });
}

/** GET /api/quant/clv/progress — progresso CLV (closed/target). */
export function fetchQuantClvProgress(signal?: AbortSignal): Promise<import("@/types/quant").ClvProgress> {
  return apiGet<import("@/types/quant").ClvProgress>("/api/quant/clv/progress", undefined, { signal });
}

/** GET /api/quant/execution/status — estado da execução (0 fills = UNKNOWN). */
export function fetchQuantExecutionStatus(signal?: AbortSignal): Promise<import("@/types/quant").ExecutionStatus> {
  return apiGet<import("@/types/quant").ExecutionStatus>("/api/quant/execution/status", undefined, { signal });
}

/** GET /api/quant/alpha-lab — artefatos do Alpha Lab (somente leitura). */
export function fetchQuantAlphaLab(signal?: AbortSignal): Promise<import("@/types/quant").QuantAlphaLab> {
  return apiGet<import("@/types/quant").QuantAlphaLab>("/api/quant/alpha-lab", undefined, { signal });
}
