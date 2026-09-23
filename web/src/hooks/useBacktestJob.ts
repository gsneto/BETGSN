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
  // vivo enquanto o hook esta montado: um poll em andamento nao pode
  // reagendar o proximo depois do unmount (vazamento de polling)
  const alive = useRef(true);
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
      if (!alive.current) return;
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
      if (!alive.current) return;
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
        if (!alive.current) return;
        // o reattach do mount pode ter reagendado um timer enquanto o
        // startBacktest estava pendente: limpa antes de agendar o novo
        stopPolling();
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

  // REATTACH: o job vive no BACKEND, nao na pagina. Se o usuario trocou
  // de aba enquanto o backtest rodava e voltou, o hook precisa adotar o
  // job em execucao em vez de fingir estado IDLE — sem isso o progresso
  // some e o resultado nao e carregado quando a execucao termina.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const current = await fetchBacktestStatus();
        if (cancelled || !alive.current) return;
        if (["preparing", "analyzing", "metrics", "saving"].includes(current.phase)) {
          setStatus(current);
          stopPolling();
          timer.current = window.setTimeout(() => void poll(), POLL_MS);
        }
      } catch {
        /* backend fora do ar: o botao de executar mostrara o erro real */
      }
    })();
    return () => {
      cancelled = true;
    };
    // so no mount: reanexar uma vez basta, o polling segue sozinho
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(
    () => () => {
      alive.current = false;
      stopPolling();
    },
    [stopPolling],
  );

  const running =
    starting ||
    ["preparing", "analyzing", "metrics", "saving"].includes(status.phase);

  return { status, running, starting, error, run, reset };
}
