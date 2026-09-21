/**
 * Cliente HTTP unico da aplicacao. Nenhum componente chama fetch()
 * diretamente: tudo passa por aqui, para que erro, timeout e parsing
 * tenham um comportamento so.
 */

import type { ApiErrorBody } from "@/types/api";

/** Base da API. Em dev o Vite faz proxy de /api para o backend FastAPI. */
export const API_BASE = import.meta.env.VITE_BETGSN_API_BASE ?? "";

/** Reexportado para quem precisa tipar o corpo de erro da API. */
export type { ApiErrorBody };

const DEFAULT_TIMEOUT_MS = 30_000;

/** Erro de API com mensagem apresentavel ao usuario (sem traceback Python). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly hint?: string;

  constructor(status: number, detail: string, hint?: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.hint = hint;
  }

  /** Mensagem curta para toast/alert. */
  get userMessage(): string {
    if (this.status === 0) {
      return "Backend BETGSN indisponivel. Verifique se a API esta rodando na porta 8787.";
    }
    if (this.status === 422) return "Parametros invalidos para o modelo.";
    if (this.status === 503) return "Pipeline indisponivel no backend.";
    return this.detail || `Falha na API (HTTP ${this.status}).`;
  }
}

interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

interface RawErrorBody {
  detail?: unknown;
  hint?: unknown;
}

/** Item de erro de validacao do FastAPI/Pydantic. */
interface ValidationItem {
  loc?: unknown[];
  msg?: string;
}

async function parseError(res: Response): Promise<ApiError> {
  let detail = res.statusText || `HTTP ${res.status}`;
  let hint: string | undefined;
  try {
    const body = (await res.json()) as RawErrorBody;
    if (typeof body.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body.detail)) {
      // erro de validacao do FastAPI/Pydantic
      detail = (body.detail as ValidationItem[])
        .map((item) => {
          const field = Array.isArray(item.loc) ? item.loc.at(-1) : undefined;
          return field ? `${String(field)}: ${item.msg ?? ""}` : (item.msg ?? "");
        })
        .filter(Boolean)
        .join("; ");
    }
    if (typeof body.hint === "string") hint = body.hint;
  } catch {
    /* corpo nao era JSON: mantem statusText */
  }
  return new ApiError(res.status, detail, hint);
}

async function request<T>(
  path: string,
  init: RequestInit,
  options: RequestOptions = {},
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  // encadeia o AbortSignal externo (cleanup do React) com o de timeout
  const onExternalAbort = () => controller.abort();
  options.signal?.addEventListener("abort", onExternalAbort);

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    if (!res.ok) throw await parseError(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      // aborto externo (unmount) deve propagar como tal
      if (options.signal?.aborted) throw err;
      throw new ApiError(0, `Tempo esgotado apos ${timeoutMs / 1000}s.`);
    }
    throw new ApiError(0, err instanceof Error ? err.message : "Falha de rede.");
  } finally {
    window.clearTimeout(timer);
    options.signal?.removeEventListener("abort", onExternalAbort);
  }
}

export function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  options?: RequestOptions,
): Promise<T> {
  let url = path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    }
    const query = qs.toString();
    if (query) url += `?${query}`;
  }
  return request<T>(url, { method: "GET" }, options);
}

export function apiPost<T>(
  path: string,
  body: unknown,
  options?: RequestOptions,
): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) }, options);
}

export function apiDelete<T>(path: string, options?: RequestOptions): Promise<T> {
  return request<T>(path, { method: "DELETE" }, options);
}

/** URL do WebSocket derivada da origem atual (respeita http/https). */
export function wsUrl(path = "/api/ws"): string {
  if (API_BASE.startsWith("http")) {
    return API_BASE.replace(/^http/, "ws") + path;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}
