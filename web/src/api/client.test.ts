/**
 * Testes do cliente HTTP.
 *
 * O contrato importante: nenhum erro chega cru ao usuario. A API devolve
 * mensagem estruturada (sem traceback Python) e o cliente a traduz numa
 * `ApiError` com mensagem apresentavel.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiDelete, apiGet, apiPost, wsUrl } from "@/api/client";
import { jsonResponse, mockFetch } from "@/test/fixtures";

describe("apiGet", () => {
  it("faz GET e devolve o JSON", async () => {
    const { calls } = mockFetch(() => jsonResponse({ ok: true }));
    const data = await apiGet<{ ok: boolean }>("/api/test");
    expect(data).toEqual({ ok: true });
    expect(calls[0].url).toBe("/api/test");
    expect(calls[0].init?.method).toBe("GET");
  });

  it("monta a query string e ignora valores vazios", async () => {
    const { calls } = mockFetch(() => jsonResponse({}));
    await apiGet("/api/x", { a: 1, b: "txt", c: undefined, d: "" });
    expect(calls[0].url).toBe("/api/x?a=1&b=txt");
  });

  it("envia Accept JSON", async () => {
    const { calls } = mockFetch(() => jsonResponse({}));
    await apiGet("/api/x");
    const headers = calls[0].init?.headers as Record<string, string>;
    expect(headers.Accept).toBe("application/json");
  });
});

describe("apiPost", () => {
  it("serializa o corpo e define Content-Type", async () => {
    const { calls } = mockFetch(() => jsonResponse({ ok: true }));
    await apiPost("/api/run", { bankroll: 1000 });
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].init?.body).toBe(JSON.stringify({ bankroll: 1000 }));
    const headers = calls[0].init?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
  });
});

describe("apiDelete", () => {
  it("faz DELETE", async () => {
    const { calls } = mockFetch(() => jsonResponse({ deleted: true }));
    await apiDelete("/api/runs/1");
    expect(calls[0].init?.method).toBe("DELETE");
  });
});

describe("tratamento de erro", () => {
  it("le a mensagem estruturada da API", async () => {
    mockFetch(() =>
      jsonResponse({ error: "ValueError", detail: "mercado desconhecido" }, 400),
    );
    await expect(apiGet("/api/x")).rejects.toThrow("mercado desconhecido");
  });

  it("formata erro de validacao do Pydantic com o campo", async () => {
    mockFetch(() =>
      jsonResponse(
        {
          detail: [
            { loc: ["body", "bankroll"], msg: "Input should be greater than 0" },
            { loc: ["body", "min_ev"], msg: "Input should be less than 1" },
          ],
        },
        422,
      ),
    );
    await expect(apiGet("/api/x")).rejects.toThrow(/bankroll.*min_ev/s);
  });

  it("nunca propaga traceback cru", async () => {
    mockFetch(
      () =>
        new Response("Traceback (most recent call last): ValueError", { status: 500 }),
    );
    const err = await apiGet("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).detail).not.toContain("Traceback");
  });

  it("traduz falha de rede em mensagem acionavel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const err = (await apiGet("/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.userMessage).toContain("indisponivel");
  });

  it("preserva o status HTTP", async () => {
    mockFetch(() => jsonResponse({ detail: "nao encontrado" }, 404));
    const err = (await apiGet("/api/x").catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(404);
  });

  it("userMessage distingue os casos principais", () => {
    expect(new ApiError(0, "x").userMessage).toContain("Backend BETGSN indisponivel");
    expect(new ApiError(422, "x").userMessage).toContain("Parametros invalidos");
    expect(new ApiError(503, "x").userMessage).toContain("Pipeline indisponivel");
    expect(new ApiError(500, "detalhe real").userMessage).toBe("detalhe real");
  });

  it("devolve undefined em 204", async () => {
    mockFetch(() => new Response(null, { status: 204 }));
    await expect(apiGet("/api/x")).resolves.toBeUndefined();
  });
});

describe("wsUrl", () => {
  beforeEach(() => {
    vi.stubGlobal("location", { protocol: "http:", host: "localhost:5180" });
  });

  it("usa ws:// em http", () => {
    expect(wsUrl("/api/ws")).toBe("ws://localhost:5180/api/ws");
  });

  it("usa wss:// em https", () => {
    vi.stubGlobal("location", { protocol: "https:", host: "exemplo.com" });
    expect(wsUrl("/api/ws")).toBe("wss://exemplo.com/api/ws");
  });
});
