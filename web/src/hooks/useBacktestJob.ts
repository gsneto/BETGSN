/**
 * useBacktestJob — dispara e acompanha uma execucao de backtest.
 *
 * O processamento acontece INTEIRO no backend (pode passar de milhares de
 * partidas). Aqui so cuidamos de:
 *  - disparar o job;
 *  - acompanhar o progresso por polling leve (2x/s), que e o que funciona
 *    mesmo se o WebSocket cair;
 *  - recarregar os resultados quando o job termina.
 *
 * Nao ha calculo estatistico no cliente em nenhum momento.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { fetchBacktestStatus, startBacktest } from "@/api/backtest";
import type { BacktestJobStatus, BacktestRequest } from "@/types/backtest";

const POLL_MS = 500;

const IDLE: BacktestJobStatus = {
  job_id: null,
  phase: "idle",
  done: 0,
  total: 0,
  progress: 0,
  message: "Pronto para executar.",
  run_id: null,
  error: null,
};

export interface BacktestJob {
  status: BacktestJobStatus;
  running: boolean;
  starting: boolean;
  error: string | null;
  run: (request: BacktestRequest) => Promise<void>;
  reset: () => void;
}

export function useBacktestJob(onFinished?: (runId: string) => void): BacktestJob {
  const [status, setStatus] = useState<BacktestJobStatus>(IDLE);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  const stopPolling = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const poll = useCallback(async () => {
    try {
      const next = await fetchBacktestStatus();
      setStatus(next);
      if (next.phase === "error") {
        setError(next.error ?? "Falha na execução do backtest.");
        stopPolling();
        return;
      }
      if (next.phase === "done") {
        stopPolling();
        if (next.run_id) onFinishedRef.current?.(next.run_id);
        return;
      }
      timer.current = window.setTimeout(() => void poll(), POLL_MS);
    } catch (err) {
      setError(err instanceof ApiError ? err.userMessage : "Falha ao consultar o progresso.");
      stopPolling();
    }
  }, [stopPolling]);

  const run = useCallback(
    async (request: BacktestRequest) => {
      stopPolling();
      setError(null);
      setStarting(true);
      try {
        const initial = await startBacktest(request);
        setStatus(initial);
        timer.current = window.setTimeout(() => void poll(), POLL_MS);
      } catch (err) {
        setError(
          err instanceof ApiError ? err.userMessage : "Falha ao iniciar o backtest.",
        );
      } finally {
        setStarting(false);
      }
    },
    [poll, stopPolling],
  );

  const reset = useCallback(() => {
    stopPolling();
    setStatus(IDLE);
    setError(null);
  }, [stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  const running =
    starting ||
    ["preparing", "analyzing", "metrics", "saving"].includes(status.phase);

  return { status, running, starting, error, run, reset };
}
