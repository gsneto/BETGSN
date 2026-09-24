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
