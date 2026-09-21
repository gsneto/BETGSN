/**
 * Testes do hook `useApiResource`.
 *
 * Contrato: um unico lugar cuida de loading/erro/cancelamento, para que
 * nenhum componente precise repetir essa logica.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";

describe("useApiResource", () => {
  it("comeca em loading e termina com os dados", async () => {
    const { result } = renderHook(() =>
      useApiResource(async () => ({ valor: 42 }), []),
    );
    expect(result.current.loading).toBe(true);
    expect(result.current.initialLoading).toBe(true);
    expect(result.current.data).toBeNull();

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual({ valor: 42 });
    expect(result.current.error).toBeNull();
    expect(result.current.initialLoading).toBe(false);
  });

  it("expõe mensagem apresentavel em ApiError", async () => {
    const { result } = renderHook(() =>
      useApiResource(async () => {
        throw new ApiError(503, "pipeline fora do ar");
      }, []),
    );
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBe("Pipeline indisponivel no backend.");
    expect(result.current.data).toBeNull();
  });

  it("usa a mensagem do Error comum", async () => {
    const { result } = renderHook(() =>
      useApiResource(async () => {
        throw new Error("falha inesperada");
      }, []),
    );
    await waitFor(() => expect(result.current.error).toBe("falha inesperada"));
  });

  it("recarrega ao chamar reload", async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }));
    const { result } = renderHook(() => useApiResource(fetcher, []));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));

    fetcher.mockResolvedValueOnce({ n: 2 });
    result.current.reload();
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("recarrega quando uma dependencia muda", async () => {
    const fetcher = vi.fn(async () => ({ n: 1 }));
    const { result, rerender } = renderHook(
      ({ dep }: { dep: number }) => useApiResource(fetcher, [dep]),
      { initialProps: { dep: 1 } },
    );
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    rerender({ dep: 2 });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(result.current.error).toBeNull();
  });

  it("nao atualiza estado apos desmontar (sem vazamento)", async () => {
    const box: { resolve: ((v: { n: number }) => void) | null } = { resolve: null };
    const { result, unmount } = renderHook(() =>
      useApiResource(
        () =>
          new Promise<{ n: number }>((resolve) => {
            box.resolve = resolve;
          }),
        [],
      ),
    );

    unmount();
    box.resolve?.({ n: 1 });
    // sem erro de "setState on unmounted component" e sem dado aplicado
    expect(result.current.data).toBeNull();
  });

  it("aborta a requisicao ao desmontar", async () => {
    // caixa mutavel: evita o narrowing do TS transformar o valor em never
    const box: { signal: AbortSignal | null } = { signal: null };
    const { unmount } = renderHook(() =>
      useApiResource(async (signal) => {
        box.signal = signal;
        return { n: 1 };
      }, []),
    );
    await waitFor(() => expect(box.signal).not.toBeNull());
    expect(box.signal?.aborted).toBe(false);

    unmount();
    expect(box.signal?.aborted).toBe(true);
  });
});
