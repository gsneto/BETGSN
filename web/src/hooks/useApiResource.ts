/**
 * useApiResource — carregamento de um recurso da API com estados de
 * loading/erro e cancelamento no unmount.
 *
 * Centralizar aqui evita espalhar fetch/useEffect por dezenas de
 * componentes e garante um unico comportamento de erro.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";

export interface ApiResource<T> {
  data: T | null;
  loading: boolean;
  /** true apenas no primeiro carregamento (para escolher skeleton vs overlay) */
  initialLoading: boolean;
  error: string | null;
  reload: () => void;
}

export function useApiResource<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
): ApiResource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const loadedOnce = useRef(false);

  // mantem a referencia do fetcher sem reexecutar o efeito a cada render
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setLoading(true);

    fetcherRef
      .current(controller.signal)
      .then((result) => {
        if (!active) return;
        setData(result);
        setError(null);
        loadedOnce.current = true;
      })
      .catch((err: unknown) => {
        if (!active || controller.signal.aborted) return;
        setError(
          err instanceof ApiError
            ? err.userMessage
            : err instanceof Error
              ? err.message
              : "Erro desconhecido.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return {
    data,
    loading,
    initialLoading: loading && !loadedOnce.current,
    error,
    reload,
  };
}
